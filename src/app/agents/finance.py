"""Finance Agent - Expense and budget management.

This agent uses a prompt-first approach: all decision logic is in the prompt,
the code only executes tools and formats responses.
"""

import asyncio
import json
import re
from typing import Any, Optional

import structlog

from ..services.quick_actions import (
    build_finance_expense_quick_actions,
    build_finance_income_quick_actions,
    build_finance_delete_expense_quick_actions,
    build_finance_modify_expense_quick_actions,
    build_finance_balance_quick_actions,
    build_finance_new_category_quick_actions,
    build_finance_report_quick_actions,
    build_finance_budget_quick_actions,
    build_finance_incomes_quick_actions,
    build_finance_search_quick_actions,
    build_finance_categories_quick_actions,
)
from ..services.quality_logger import get_quality_logger
from .base import (
    FIRST_TIME_TOOL_DEFINITION,
    FIRST_TIME_TOOL_DEFINITION_ANTHROPIC,
    AgentResult,
    BaseAgent,
    ToolOutput,
    openai_tool_to_anthropic,
)
from .tools.finance_executor import (
    execute_finance_tool,
    format_finance_response,
    format_finance_tool_result_for_llm,
)
from .tools.registry import FINANCE_TOOLS

logger = structlog.get_logger()


class FinanceAgent(BaseAgent):
    """Agent for managing expenses and budgets.
    
    Decision logic is handled by the LLM through the prompt.
    This code only:
    - Executes HTTP calls to backend APIs
    - Formats responses for WhatsApp
    - Handles technical errors
    """

    name = "finance"

    def __init__(self):
        """Initialize the finance agent."""
        super().__init__()
        self._init_llm_client("finance_model_provider")

    @staticmethod
    def _extract_expense_id(text: str) -> Optional[str]:
        """Extract expense_id from synthetic quick-action text markers."""
        patterns = [
            r"expense_id=([0-9a-fA-F-]{36})",
            r"PENDING_EXPENSE_EDIT_ID=([0-9a-fA-F-]{36})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    def _build_metadata(
        self,
        tool_name: str,
        tool_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Build standardized metadata for logging and quick actions."""
        metadata: dict[str, Any] = {"tool": tool_name, "result": tool_result}
        data = tool_result.get("data", {}) if isinstance(tool_result, dict) else {}
        success = isinstance(data, dict) and data.get("success") is not False

        if (
            tool_name == "registrar_gasto"
            and isinstance(data, dict)
            and data.get("success") is True
            and data.get("expense_id")
        ):
            metadata["quick_actions"] = build_finance_expense_quick_actions(
                str(data["expense_id"])
            )
        elif (
            tool_name == "registrar_ingreso"
            and isinstance(data, dict)
            and data.get("success") is True
            and data.get("income_id")
        ):
            metadata["quick_actions"] = build_finance_income_quick_actions(
                str(data["income_id"])
            )
        elif tool_name == "eliminar_gasto" and success:
            deleted = data.get("deleted_expense", {}) if isinstance(data, dict) else {}
            metadata["quick_actions"] = build_finance_delete_expense_quick_actions(
                str(deleted.get("amount", "")), deleted.get("category", "")
            )
        elif tool_name == "modificar_gasto" and success:
            metadata["quick_actions"] = build_finance_modify_expense_quick_actions()
        elif tool_name == "eliminar_ingreso" and success:
            metadata["quick_actions"] = build_finance_balance_quick_actions()
        elif tool_name == "modificar_ingreso" and success:
            metadata["quick_actions"] = build_finance_balance_quick_actions()
        elif tool_name == "crear_categoria" and success:
            metadata["quick_actions"] = build_finance_new_category_quick_actions()

        return metadata

    @staticmethod
    def _build_continue_quick_actions(last_tool_name: Optional[str]) -> Optional[dict[str, Any]]:
        """Build quick actions metadata for continue-tools when LLM responds with text."""
        if not last_tool_name:
            return None
        qa_map = {
            "consultar_reporte": build_finance_report_quick_actions,
            "consultar_presupuesto": build_finance_budget_quick_actions,
            "consultar_balance": build_finance_balance_quick_actions,
            "consultar_ingresos": build_finance_incomes_quick_actions,
            "buscar_gastos": build_finance_search_quick_actions,
            "listar_categorias": build_finance_categories_quick_actions,
        }
        builder = qa_map.get(last_tool_name)
        if builder:
            return {"quick_actions": builder()}
        return None

    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs,
    ) -> AgentResult:
        """Process a finance-related message.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.

        Returns:
            The agent's response.
        """
        logger.info("Finance agent processing", message=message[:50])
        quality_logger = get_quality_logger()

        prompt = await self.get_prompt(tenant_id)

        # Inject current date so the LLM knows what "this month" means
        from datetime import date as _date
        today = _date.today()
        date_context = f"\n\n[FECHA_ACTUAL] Hoy es {today.strftime('%d/%m/%Y')} ({today.strftime('%A')}). Mes actual: {today.month}, Año: {today.year}."

        # Build messages
        messages = [
            {"role": "system", "content": prompt + date_context},
        ]

        # Add history context
        for msg in history[-6:]:
            role = msg["role"] if isinstance(msg, dict) else msg.role
            content = msg["content"] if isinstance(msg, dict) else msg.content
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": f"[USER_MSG]{message}[/USER_MSG]"})

        # Check first-time use
        is_first_time = await self.check_first_time(phone)
        if is_first_time:
            messages.insert(1, {
                "role": "system",
                "content": (
                    "[PRIMERA_VEZ] Este es el primer uso del usuario con este módulo. "
                    "Seguí las instrucciones de la sección 'Primera Vez' del prompt."
                ),
            })

        # Define tools
        tools = list(FINANCE_TOOLS)  # copy to avoid mutation

        if is_first_time:
            tools.append(FIRST_TIME_TOOL_DEFINITION)

        try:
            quick_action_expense_id = self._extract_expense_id(message)
            lowered = message.lower()

            # Deterministic quick action: delete just-created expense by id.
            if lowered.startswith("eliminar gasto con expense_id=") and quick_action_expense_id:
                tool_name = "eliminar_gasto"
                tool_args = {"expense_id": quick_action_expense_id}
                tool_result = await execute_finance_tool(
                    tool_name, tool_args, tenant_id,
                    phone=phone,
                    message_in=message,
                )
                response_text = format_finance_response(tool_name, tool_args, tool_result)
                tool_output = ToolOutput(
                    success=tool_result.get("success", False),
                    domain="finance",
                    tool_name=tool_name,
                    tool_args=tool_args,
                    data=tool_result.get("data", {}),
                    formatted_text=response_text,
                    quick_actions=self._build_metadata(tool_name, tool_result).get("quick_actions"),
                )
                return AgentResult(
                    response=response_text,
                    agent_used=self.name,
                    metadata=self._build_metadata(tool_name, tool_result),
                    tool_output=tool_output,
                )

            # Deterministic quick action: open edit flow targeting one expense.
            if lowered.startswith("editar gasto con expense_id=") and quick_action_expense_id:
                return AgentResult(
                    response=(
                        "Perfecto. Decime qué querés editar de ese gasto: monto, categoría, descripción o fecha. "
                        "Ejemplo: 'cambiá el monto a 18500'."
                    ),
                    agent_used=self.name,
                    metadata={
                        "tool": "quick_edit_start",
                        "expense_id": quick_action_expense_id,
                    },
                )

            total_tokens_in = 0
            total_tokens_out = 0
            max_tool_rounds = 5

            if self.provider == "anthropic":
                # --- Anthropic path ---
                anthropic_tools = [openai_tool_to_anthropic(t) for t in tools]

                system_text, filtered_msgs = self._extract_system_and_messages(messages)
                last_tool_name = None

                for _ in range(max_tool_rounds):
                    response = await self.client.messages.create(
                        model=self.settings.anthropic_subagent_model,
                        system=system_text,
                        messages=filtered_msgs,
                        tools=anthropic_tools,
                        tool_choice={"type": "auto"},
                        max_tokens=1000,
                    )

                    t_in, t_out = self._anthropic_tokens(response)
                    total_tokens_in += t_in
                    total_tokens_out += t_out

                    # Log to Langfuse (nested under supervisor trace)
                    self._log_generation(
                        name="finance-generation",
                        model=self.settings.anthropic_subagent_model,
                        input_msgs=filtered_msgs[-2:],
                        output_content=[
                            {"type": getattr(b, "type", ""), "text": getattr(b, "text", str(b))}
                            for b in response.content
                        ],
                        usage_in=t_in,
                        usage_out=t_out,
                        metadata={"stop_reason": response.stop_reason},
                    )

                    # LLM returned text (no tool call) -> final response
                    if response.stop_reason != "tool_use":
                        text = self._extract_text(response)
                        qa_metadata = self._build_continue_quick_actions(last_tool_name)
                        return AgentResult(
                            response=text or "No pude procesar tu solicitud.",
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            metadata=qa_metadata,
                        )

                    # LLM called tool(s) -> execute ALL, append results, loop or return
                    tool_uses = self._extract_all_tool_uses(response)
                    if not tool_uses:
                        text = self._extract_text(response)
                        qa_metadata = self._build_continue_quick_actions(last_tool_name)
                        return AgentResult(
                            response=text or "No pude procesar tu solicitud.",
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            metadata=qa_metadata,
                        )

                    for tu_name, tu_args, _ in tool_uses:
                        logger.info(f"Finance tool call: {tu_name}", args=tu_args)

                    # Execute all tools in parallel
                    tool_results = await asyncio.gather(*[
                        execute_finance_tool(
                            tu_name, tu_args, tenant_id,
                            phone=phone, message_in=message,
                        )
                        for tu_name, tu_args, _ in tool_uses
                    ])

                    # Handle first-time completion markers
                    resolved_results: list[dict[str, Any]] = []
                    for tr in tool_results:
                        if isinstance(tr.get("data"), dict) and tr["data"].get("first_time_tool"):
                            result_msg = await self.complete_first_time(phone)
                            resolved_results.append({"success": True, "data": {"message": result_msg}})
                        else:
                            resolved_results.append(tr)

                    # Append assistant message with all tool_use blocks
                    filtered_msgs.append(
                        {"role": "assistant", "content": response.content}
                    )

                    # Append all tool results in a single user message
                    tool_results_content = []
                    for (tu_name, tu_args, tu_id), tr in zip(tool_uses, resolved_results):
                        formatted = format_finance_tool_result_for_llm(tu_name, tu_args, tr)
                        tool_results_content.append({
                            "type": "tool_result",
                            "tool_use_id": tu_id,
                            "content": formatted,
                        })
                    filtered_msgs.append({"role": "user", "content": tool_results_content})

                    # Tools that need LLM follow-up
                    _CONTINUE_TOOLS = {
                        "consultar_presupuesto", "consultar_reporte",
                        "completar_configuracion_inicial", "listar_categorias",
                        "consultar_ingresos", "consultar_balance", "buscar_gastos",
                    }

                    if any(tu_name in _CONTINUE_TOOLS for tu_name, _, _ in tool_uses):
                        last_tool_name = tool_uses[-1][0]
                        continue

                    # All tools are immediate-return — build result
                    if len(tool_uses) == 1:
                        tu_name, tu_args, _ = tool_uses[0]
                        tr = resolved_results[0]
                        response_text = format_finance_response(tu_name, tu_args, tr)
                        tool_output = ToolOutput(
                            success=tr.get("success", False),
                            domain="finance",
                            tool_name=tu_name,
                            tool_args=tu_args,
                            data=tr.get("data", {}),
                            formatted_text=response_text,
                            quick_actions=self._build_metadata(tu_name, tr).get("quick_actions"),
                        )
                        return AgentResult(
                            response=response_text,
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            metadata=self._build_metadata(tu_name, tr),
                            tool_output=tool_output,
                        )

                    # Multiple immediate tools — combined ToolOutput
                    combined_data = {
                        "operations": [
                            {"tool": tu_name, "args": tu_args, "result": tr}
                            for (tu_name, tu_args, _), tr in zip(tool_uses, resolved_results)
                        ],
                    }
                    combined_texts = [
                        format_finance_response(tu_name, tu_args, tr)
                        for (tu_name, tu_args, _), tr in zip(tool_uses, resolved_results)
                    ]
                    last_tu_name = tool_uses[-1][0]
                    last_tr = resolved_results[-1]
                    tool_output = ToolOutput(
                        success=all(tr.get("success", False) for tr in resolved_results),
                        domain="finance",
                        tool_name="multi_tool",
                        tool_args={},
                        data=combined_data,
                        formatted_text="\n\n".join(combined_texts),
                        quick_actions=self._build_metadata(last_tu_name, last_tr).get("quick_actions"),
                    )
                    return AgentResult(
                        response=tool_output.formatted_text,
                        agent_used=self.name,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        metadata=self._build_metadata(last_tu_name, last_tr),
                        tool_output=tool_output,
                    )

            else:
                # --- OpenAI path (rollback) ---
                last_tool_name = None
                for _ in range(max_tool_rounds):
                    response = await self.client.chat.completions.create(
                        model=self.settings.openai_model,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        temperature=0.4,
                        max_completion_tokens=1000,
                    )

                    choice = response.choices[0]
                    if response.usage:
                        total_tokens_in += response.usage.prompt_tokens
                        total_tokens_out += response.usage.completion_tokens

                    # Log to Langfuse
                    self._log_generation(
                        name="finance-generation",
                        model=self.settings.openai_model,
                        input_msgs=messages[-2:],
                        output_content=choice.message.model_dump() if choice else {},
                        usage_in=response.usage.prompt_tokens if response.usage else 0,
                        usage_out=response.usage.completion_tokens if response.usage else 0,
                    )

                    # LLM returned text (no tool call) -> final response
                    if not choice.message.tool_calls:
                        qa_metadata = self._build_continue_quick_actions(last_tool_name)
                        return AgentResult(
                            response=choice.message.content or "No pude procesar tu solicitud.",
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            metadata=qa_metadata,
                        )

                    # LLM called tool(s) -> execute ALL, append results, loop or return
                    all_tool_calls = choice.message.tool_calls
                    for tc in all_tool_calls:
                        logger.info(f"Finance tool call: {tc.function.name}", args=tc.function.arguments[:200])

                    # Parse all tool calls
                    parsed_calls = [
                        (tc.function.name, json.loads(tc.function.arguments), tc.id)
                        for tc in all_tool_calls
                    ]

                    # Execute all tools in parallel
                    tool_results = await asyncio.gather(*[
                        execute_finance_tool(
                            tc_name, tc_args, tenant_id,
                            phone=phone, message_in=message,
                        )
                        for tc_name, tc_args, _ in parsed_calls
                    ])

                    # Handle first-time completion markers
                    resolved_results: list[dict[str, Any]] = []
                    for tr in tool_results:
                        if isinstance(tr.get("data"), dict) and tr["data"].get("first_time_tool"):
                            result_msg = await self.complete_first_time(phone)
                            resolved_results.append({"success": True, "data": {"message": result_msg}})
                        else:
                            resolved_results.append(tr)

                    # Append assistant message with all tool_calls
                    messages.append(
                        {
                            "role": "assistant",
                            "content": choice.message.content or None,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in all_tool_calls
                            ],
                        }
                    )

                    # Append all tool results
                    for (tc_name, tc_args, tc_id), tr in zip(parsed_calls, resolved_results):
                        formatted = format_finance_tool_result_for_llm(tc_name, tc_args, tr)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": formatted,
                        })

                    # Tools that need LLM follow-up
                    _CONTINUE_TOOLS = {
                        "consultar_presupuesto", "consultar_reporte",
                        "completar_configuracion_inicial", "listar_categorias",
                        "consultar_ingresos", "consultar_balance", "buscar_gastos",
                    }

                    if any(tc_name in _CONTINUE_TOOLS for tc_name, _, _ in parsed_calls):
                        last_tool_name = parsed_calls[-1][0]
                        continue

                    # All tools are immediate-return — build result
                    if len(parsed_calls) == 1:
                        tc_name, tc_args, _ = parsed_calls[0]
                        tr = resolved_results[0]
                        response_text = format_finance_response(tc_name, tc_args, tr)
                        tool_output = ToolOutput(
                            success=tr.get("success", False),
                            domain="finance",
                            tool_name=tc_name,
                            tool_args=tc_args,
                            data=tr.get("data", {}),
                            formatted_text=response_text,
                            quick_actions=self._build_metadata(tc_name, tr).get("quick_actions"),
                        )
                        return AgentResult(
                            response=response_text,
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            metadata=self._build_metadata(tc_name, tr),
                            tool_output=tool_output,
                        )

                    # Multiple immediate tools — combined ToolOutput
                    combined_data = {
                        "operations": [
                            {"tool": tc_name, "args": tc_args, "result": tr}
                            for (tc_name, tc_args, _), tr in zip(parsed_calls, resolved_results)
                        ],
                    }
                    combined_texts = [
                        format_finance_response(tc_name, tc_args, tr)
                        for (tc_name, tc_args, _), tr in zip(parsed_calls, resolved_results)
                    ]
                    last_tc_name = parsed_calls[-1][0]
                    last_tr = resolved_results[-1]
                    tool_output = ToolOutput(
                        success=all(tr.get("success", False) for tr in resolved_results),
                        domain="finance",
                        tool_name="multi_tool",
                        tool_args={},
                        data=combined_data,
                        formatted_text="\n\n".join(combined_texts),
                        quick_actions=self._build_metadata(last_tc_name, last_tr).get("quick_actions"),
                    )
                    return AgentResult(
                        response=tool_output.formatted_text,
                        agent_used=self.name,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        metadata=self._build_metadata(last_tc_name, last_tr),
                        tool_output=tool_output,
                    )

            # Max rounds reached
            return AgentResult(
                response="No pude completar la operación. Intentá de nuevo.",
                agent_used=self.name,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
            )

        except Exception as e:
            logger.error("Finance agent error", error=str(e))
            await quality_logger.log_hard_error(
                tenant_id=tenant_id,
                category="llm_error",
                error_message=str(e),
                agent_name=self.name,
                user_phone=phone,
                message_in=message,
                severity="high",
                exception=e,
            )
            return AgentResult(
                response="Hubo un problema procesando tu solicitud. Intentá de nuevo.",
                agent_used=self.name,
            )

