"""Finance Agent - Expense and budget management.

This agent uses a prompt-first approach: all decision logic is in the prompt,
the code only executes tools and formats responses.
"""

import json
import re
from typing import Any, Optional

import structlog

from ..config import get_settings
from ..services.backend_client import get_backend_client
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
    openai_tool_to_anthropic,
)

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
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "registrar_gasto",
                    "description": "Registra un nuevo gasto. La categoría debe existir.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number", "description": "Monto del gasto"},
                            "category": {"type": "string", "description": "Categoría del gasto (debe existir)"},
                            "description": {"type": "string", "description": "Lo que el usuario menciona sobre el gasto (ej: combustible, verdulería, algo raro). Siempre incluir cuando el usuario lo diga."},
                            "expense_date": {"type": "string", "description": "Fecha YYYY-MM-DD (opcional)"},
                        },
                        "required": ["amount", "category"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "consultar_reporte",
                    "description": "Consulta reporte de gastos por período",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "period": {
                                "type": "string",
                                "enum": ["day", "week", "month", "year"],
                                "description": "Período del reporte",
                            },
                            "category": {"type": "string", "description": "Filtrar por categoría"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "consultar_presupuesto",
                    "description": "Consulta estado del presupuesto y lista las categorías existentes",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "Categoría específica (opcional)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "eliminar_gasto",
                    "description": "Elimina un gasto específico",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expense_id": {"type": "string", "description": "ID exacto del gasto a eliminar (preferido)"},
                            "amount": {"type": "number", "description": "Monto del gasto"},
                            "category": {"type": "string", "description": "Categoría del gasto"},
                            "description": {"type": "string", "description": "Descripción del gasto"},
                            "expense_date": {"type": "string", "description": "Fecha YYYY-MM-DD"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "eliminar_gasto_masivo",
                    "description": "Elimina múltiples gastos de un período",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "period": {
                                "type": "string",
                                "enum": ["today", "week", "month", "year", "all"],
                                "description": "Período a eliminar",
                            },
                            "category": {"type": "string", "description": "Categoría específica"},
                            "confirm": {"type": "boolean", "description": "Confirmación requerida"},
                        },
                        "required": ["period", "confirm"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "modificar_gasto",
                    "description": "Modifica un gasto existente",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expense_id": {"type": "string", "description": "ID exacto del gasto a modificar (preferido)"},
                            "search_amount": {"type": "number", "description": "Monto actual"},
                            "search_category": {"type": "string", "description": "Categoría actual"},
                            "search_description": {"type": "string", "description": "Descripción actual"},
                            "search_date": {"type": "string", "description": "Fecha actual"},
                            "new_amount": {"type": "number", "description": "Nuevo monto"},
                            "new_category": {"type": "string", "description": "Nueva categoría"},
                            "new_description": {"type": "string", "description": "Nueva descripción"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fijar_presupuesto",
                    "description": "Fija o actualiza el presupuesto mensual de una categoría",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category_id": {"type": "string", "description": "ID de categoría (opcional, preferido para updates determinísticos)"},
                            "category": {"type": "string", "description": "Nombre de la categoría"},
                            "monthly_limit": {"type": "number", "description": "Límite mensual en pesos"},
                            "alert_threshold": {
                                "type": "integer",
                                "description": "Porcentaje de alerta (default: 80)",
                                "default": 80,
                            },
                        },
                        "required": ["category", "monthly_limit"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "eliminar_presupuesto",
                    "description": "Elimina el presupuesto de una categoría (sin borrar la categoría)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category_id": {"type": "string", "description": "ID de categoría"},
                            "category": {"type": "string", "description": "Nombre de categoría"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "listar_categorias",
                    "description": "Lista categorías disponibles y su estado",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "crear_categoria",
                    "description": "Crea una nueva categoría",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Nombre de la categoría"},
                            "monthly_limit": {"type": "number", "description": "Presupuesto mensual opcional"},
                            "alert_threshold": {"type": "integer", "description": "Umbral de alerta 0-100"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "editar_categoria",
                    "description": "Edita una categoría existente",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category_id": {"type": "string", "description": "ID de categoría"},
                            "category_name": {"type": "string", "description": "Nombre actual de categoría"},
                            "new_name": {"type": "string", "description": "Nuevo nombre"},
                            "monthly_limit": {"type": "number", "description": "Nuevo límite mensual"},
                            "alert_threshold": {"type": "integer", "description": "Nuevo umbral de alerta"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "eliminar_categoria",
                    "description": "Elimina una categoría (solo si no tiene gastos asociados)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category_id": {"type": "string", "description": "ID de categoría"},
                            "category_name": {"type": "string", "description": "Nombre de categoría"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "registrar_ingreso",
                    "description": "Registra un ingreso (sueldo, cobro, etc.)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number", "description": "Monto del ingreso"},
                            "description": {"type": "string", "description": "Descripcion del ingreso"},
                            "income_date": {"type": "string", "description": "Fecha YYYY-MM-DD"},
                        },
                        "required": ["amount"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "consultar_ingresos",
                    "description": "Lista ingresos del periodo",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "period": {
                                "type": "string",
                                "enum": ["day", "week", "month", "year"],
                                "description": "Periodo",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "eliminar_ingreso",
                    "description": "Elimina un ingreso",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "income_id": {"type": "string", "description": "ID del ingreso"},
                            "amount": {"type": "number", "description": "Monto del ingreso"},
                            "description": {"type": "string", "description": "Descripcion"},
                            "income_date": {"type": "string", "description": "Fecha YYYY-MM-DD"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "modificar_ingreso",
                    "description": "Modifica un ingreso existente",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "income_id": {"type": "string", "description": "ID del ingreso"},
                            "search_amount": {"type": "number", "description": "Monto actual"},
                            "search_description": {"type": "string", "description": "Descripcion actual"},
                            "new_amount": {"type": "number", "description": "Nuevo monto"},
                            "new_description": {"type": "string", "description": "Nueva descripcion"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "consultar_balance",
                    "description": "Muestra balance del mes: ingresos vs gastos",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "month": {"type": "integer", "description": "Mes 1-12 (opcional)"},
                            "year": {"type": "integer", "description": "Anio (opcional)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "buscar_gastos",
                    "description": "Busca gastos por criterios (monto, descripcion, fecha, categoria)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number", "description": "Monto del gasto"},
                            "description": {"type": "string", "description": "Texto en descripcion"},
                            "expense_date": {"type": "string", "description": "Fecha YYYY-MM-DD"},
                            "category": {"type": "string", "description": "Categoria"},
                            "limit": {"type": "integer", "description": "Maximo resultados (default 5)"},
                        },
                    },
                },
            },
        ]

        if is_first_time:
            tools.append(FIRST_TIME_TOOL_DEFINITION)

        try:
            quick_action_expense_id = self._extract_expense_id(message)
            lowered = message.lower()

            # Deterministic quick action: delete just-created expense by id.
            if lowered.startswith("eliminar gasto con expense_id=") and quick_action_expense_id:
                tool_name = "eliminar_gasto"
                tool_args = {"expense_id": quick_action_expense_id}
                tool_result = await self._execute_tool(
                    tool_name, tool_args, tenant_id,
                    user_phone=phone,
                    message_in=message,
                )
                return AgentResult(
                    response=self._format_response(tool_name, tool_args, tool_result),
                    agent_used=self.name,
                    metadata=self._build_metadata(tool_name, tool_result),
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

                    # LLM called a tool -> execute, append result, loop again
                    tool_info = self._extract_tool_use(response)
                    if not tool_info:
                        text = self._extract_text(response)
                        qa_metadata = self._build_continue_quick_actions(last_tool_name)
                        return AgentResult(
                            response=text or "No pude procesar tu solicitud.",
                            agent_used=self.name,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            metadata=qa_metadata,
                        )

                    tool_name, tool_args, tool_use_id = tool_info

                    logger.info(f"Finance tool call: {tool_name}", args=tool_args)

                    tool_result = await self._execute_tool(
                        tool_name, tool_args, tenant_id,
                        user_phone=phone,
                        message_in=message,
                    )

                    tool_result_for_llm = self._format_tool_result_for_llm(
                        tool_name, tool_args, tool_result
                    )

                    # Append assistant message with raw content blocks
                    filtered_msgs.append(
                        {"role": "assistant", "content": response.content}
                    )
                    # Append tool result
                    filtered_msgs.append(
                        self._build_tool_result_msg(tool_use_id, tool_result_for_llm)
                    )

                    # These tools need LLM follow-up: continue loop so it can reason.
                    if tool_name in (
                        "consultar_presupuesto",
                        "consultar_reporte",
                        "completar_configuracion_inicial",
                        "listar_categorias",
                        "consultar_ingresos",
                        "consultar_balance",
                        "buscar_gastos",
                    ):
                        last_tool_name = tool_name
                        continue

                    # Other tools: return formatted response
                    response_text = self._format_response(tool_name, tool_args, tool_result)
                    return AgentResult(
                        response=response_text,
                        agent_used=self.name,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        metadata=self._build_metadata(tool_name, tool_result),
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

                    # LLM called a tool -> execute, append result, loop again
                    tool_call = choice.message.tool_calls[0]
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    logger.info(f"Finance tool call: {tool_name}", args=tool_args)

                    tool_result = await self._execute_tool(
                        tool_name, tool_args, tenant_id,
                        user_phone=phone,
                        message_in=message,
                    )

                    # Append assistant message with tool_calls
                    messages.append(
                        {
                            "role": "assistant",
                            "content": choice.message.content or None,
                            "tool_calls": [
                                {
                                    "id": tool_call.id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": tool_call.function.arguments,
                                    },
                                }
                            ],
                        }
                    )

                    # Append tool result for LLM to reason about
                    tool_result_for_llm = self._format_tool_result_for_llm(
                        tool_name, tool_args, tool_result
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result_for_llm,
                        }
                    )

                    # These tools need LLM follow-up: continue loop so it can reason.
                    if tool_name in (
                        "consultar_presupuesto",
                        "consultar_reporte",
                        "completar_configuracion_inicial",
                        "listar_categorias",
                        "consultar_ingresos",
                        "consultar_balance",
                        "buscar_gastos",
                    ):
                        last_tool_name = tool_name
                        continue

                    # Other tools: return formatted response
                    response_text = self._format_response(tool_name, tool_args, tool_result)
                    return AgentResult(
                        response=response_text,
                        agent_used=self.name,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        metadata=self._build_metadata(tool_name, tool_result),
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

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        tenant_id: str,
        user_phone: Optional[str] = None,
        message_in: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a finance tool by calling the backend API.
        
        This method ONLY executes the API call - no business logic decisions.

        Args:
            tool_name: Name of the tool.
            args: Tool arguments.
            tenant_id: The tenant ID.
            user_phone: User's phone for error logging.
            message_in: Original message for error logging.

        Returns:
            Tool execution result.
        """
        base_path = f"/api/v1/tenants/{tenant_id}"
        backend = get_backend_client()
        quality_logger = get_quality_logger()

        try:
            if tool_name == "registrar_gasto":
                response = await backend.post(
                    f"{base_path}/agent/expense", params=args,
                )
            elif tool_name == "consultar_reporte":
                response = await backend.get(
                    f"{base_path}/agent/report", params=args,
                )
            elif tool_name == "consultar_presupuesto":
                response = await backend.get(
                    f"{base_path}/agent/budget", params=args,
                )
            elif tool_name == "eliminar_gasto":
                response = await backend.delete(
                    f"{base_path}/agent/expense", params=args,
                )
            elif tool_name == "eliminar_gasto_masivo":
                response = await backend.delete(
                    f"{base_path}/agent/expenses/bulk", params=args,
                )
            elif tool_name == "modificar_gasto":
                response = await backend.patch(
                    f"{base_path}/agent/expense", params=args,
                )
            elif tool_name == "fijar_presupuesto":
                response = await backend.put(
                    f"{base_path}/agent/budget", params=args,
                )
            elif tool_name == "eliminar_presupuesto":
                response = await backend.delete(
                    f"{base_path}/agent/budget", params=args,
                )
            elif tool_name == "listar_categorias":
                response = await backend.get(
                    f"{base_path}/agent/categories", params=args,
                )
            elif tool_name == "crear_categoria":
                response = await backend.post(
                    f"{base_path}/agent/category", params=args,
                )
            elif tool_name == "editar_categoria":
                response = await backend.patch(
                    f"{base_path}/agent/category", params=args,
                )
            elif tool_name == "eliminar_categoria":
                response = await backend.delete(
                    f"{base_path}/agent/category", params=args,
                )
            elif tool_name == "registrar_ingreso":
                response = await backend.post(
                    f"{base_path}/agent/income", params=args,
                )
            elif tool_name == "consultar_ingresos":
                response = await backend.get(
                    f"{base_path}/agent/incomes", params=args,
                )
            elif tool_name == "eliminar_ingreso":
                response = await backend.delete(
                    f"{base_path}/agent/income", params=args,
                )
            elif tool_name == "modificar_ingreso":
                response = await backend.patch(
                    f"{base_path}/agent/income", params=args,
                )
            elif tool_name == "consultar_balance":
                from datetime import date as _date
                today = _date.today()
                balance_params = {
                    "month": args.get("month") or today.month,
                    "year": args.get("year") or today.year,
                }
                response = await backend.get(
                    f"{base_path}/finance/overview", params=balance_params,
                )
            elif tool_name == "buscar_gastos":
                response = await backend.get(
                    f"{base_path}/agent/expenses/search", params=args,
                )
            elif tool_name == "completar_configuracion_inicial":
                result_msg = await self.complete_first_time(user_phone or "")
                return {"success": True, "data": {"message": result_msg}}
            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}

            if response.status_code in (200, 201):
                return {"success": True, "data": response.json()}
            else:
                error_text = response.text[:500] if response.text else "No response body"
                await quality_logger.log_hard_error(
                    tenant_id=tenant_id,
                    category="api_error",
                    error_message=f"Backend API returned {response.status_code}: {error_text}",
                    error_code=str(response.status_code),
                    agent_name=self.name,
                    tool_name=tool_name,
                    user_phone=user_phone,
                    message_in=message_in,
                    severity="high" if response.status_code >= 500 else "medium",
                    request_payload={"tool_args": args},
                )
                return {
                    "success": False,
                    "error": error_text,
                    "status_code": response.status_code,
                }

        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name}", error=str(e))
            await quality_logger.log_hard_error(
                tenant_id=tenant_id,
                category="api_error",
                error_message=str(e),
                agent_name=self.name,
                tool_name=tool_name,
                user_phone=user_phone,
                message_in=message_in,
                severity="high",
                exception=e,
            )
            return {"success": False, "error": str(e)}

    def _format_tool_result_for_llm(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        """Format tool result for LLM to reason about (not for user display)."""
        if not result.get("success"):
            return f"Error: {result.get('error', 'Error desconocido')}"

        data = result.get("data", {})
        if isinstance(data, dict) and data.get("success") is False:
            return data.get("message") or "❌ No pude completar la operación."

        if tool_name == "consultar_presupuesto":
            budgets = data.get("budgets", [])
            if not budgets:
                return "No hay categorías configuradas."
            categories = [b.get("category", "") for b in budgets]
            lines = [f"Categorías disponibles: {', '.join(categories)}"]
            for b in budgets:
                name = b.get("category", "")
                limit = float(b.get("limit", 0) or 0)
                spent = float(b.get("spent", 0) or 0)
                remaining = float(b.get("remaining", 0) or 0)
                lines.append(f"- {name}: límite ${limit:,.0f}/mes, gastado ${spent:,.0f}, restante ${remaining:,.0f}")
            return "\n".join(lines)

        if tool_name == "listar_categorias":
            categories = data.get("categories", [])
            if not categories:
                return "No hay categorías configuradas."
            lines = ["Categorías disponibles:"]
            for cat in categories:
                lines.append(f"- {cat.get('name', '')}")
            return "\n".join(lines)

        if tool_name == "consultar_ingresos":
            incomes = data.get("incomes", [])
            if not incomes:
                return "No hay ingresos registrados en este período."
            total = float(data.get("total", 0))
            lines = [f"Ingresos del período (total: ${total:,.0f}):"]
            for inc in incomes:
                desc = inc.get("description") or "Sin descripción"
                amt = float(inc.get("amount", 0))
                dt = inc.get("income_date", "")
                lines.append(f"- ${amt:,.0f} - {desc} ({dt})")
            return "\n".join(lines)

        if tool_name == "consultar_balance":
            total_income = float(data.get("total_income", 0))
            total_expense = float(data.get("total_expense", 0))
            balance = float(data.get("balance", 0))
            comparison = data.get("comparison_previous_month")
            lines = [
                f"Balance del mes {data.get('month', '')}:",
                f"- Ingresos: ${total_income:,.0f}",
                f"- Gastos: ${total_expense:,.0f}",
                f"- Balance: ${balance:,.0f}",
            ]
            if comparison is not None:
                pct = comparison * 100
                lines.append(f"- vs mes anterior: {pct:+.0f}% en gastos")
            groups = data.get("groups", [])
            if groups:
                lines.append("Desglose por grupo:")
                for g in groups[:5]:
                    lines.append(f"  - {g.get('name', '')}: ${float(g.get('total_spent', 0)):,.0f}")
            return "\n".join(lines)

        if tool_name == "buscar_gastos":
            expenses = data.get("expenses", [])
            if not expenses:
                return "No se encontraron gastos con esos criterios."
            lines = [f"Se encontraron {len(expenses)} gasto(s):"]
            for e in expenses:
                amt = float(e.get("amount", 0))
                cat = e.get("category_name", "Sin categoría")
                desc = e.get("description") or ""
                dt = e.get("expense_date", "")
                lines.append(f"- ${amt:,.0f} en {cat} - {desc} ({dt}) [id: {e.get('id', '')}]")
            return "\n".join(lines)

        return json.dumps(data, ensure_ascii=False)

    def _format_response(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        """Format tool result for WhatsApp display.
        
        This method ONLY formats data - no business logic decisions.

        Args:
            tool_name: Name of the tool.
            args: Tool arguments.
            result: Tool execution result.

        Returns:
            Formatted response string.
        """
        if not result.get("success"):
            error = result.get("error", "Error desconocido")
            return f"❌ No pude completar la operación: {error}"

        data = result.get("data", {})

        if tool_name == "registrar_gasto":
            amount = args.get("amount", 0)
            category = args.get("category", "")
            budget_status = data.get("budget_status")
            assigned_group = data.get("assigned_group")
            assigned_subcategory = data.get("assigned_subcategory") or category

            response = data.get("message")
            if not response:
                assignment_line = (
                    f"📌 Lo asigné a {assigned_group} > {assigned_subcategory}."
                    if assigned_group
                    else f"📌 Lo asigné a {assigned_subcategory}."
                )
                response = f"✅ Registré un gasto de ${amount:,.0f}.\n{assignment_line}"
                if assigned_group:
                    response += f"\n👉 ¿Querés definir o ajustar el presupuesto mensual de {assigned_group}?"
                else:
                    response += "\n👉 ¿Querés ver el resumen del mes o cargar otro gasto?"
            
            if budget_status:
                remaining = float(budget_status.get("remaining", 0))
                spent = float(budget_status.get("spent_this_month", 0))
                limit = float(budget_status.get("monthly_limit", 0))
                pct = float(budget_status.get("percentage_used", 0))
                
                if pct >= 100:
                    response += f"\n\n🔴 Presupuesto EXCEDIDO en {assigned_subcategory}."
                    response += f"\n   Límite: ${limit:,.0f} | Gastado: ${spent:,.0f}"
                elif pct >= 80:
                    response += f"\n\n⚠️ Te quedan ${remaining:,.0f} de ${limit:,.0f} en {assigned_subcategory} ({pct:.0f}%)"
                else:
                    response += f"\n\n💰 Te quedan ${remaining:,.0f} de ${limit:,.0f} en {assigned_subcategory} ({pct:.0f}%)"
            
            return response

        elif tool_name == "consultar_reporte":
            if not data:
                return "📊 No hay gastos registrados en este período."

            total = float(data.get("total_spent", 0) or 0)
            by_category = data.get("by_category", [])
            period = args.get("period", "month")

            period_names = {
                "day": "hoy",
                "week": "esta semana",
                "month": "este mes",
                "year": "este año",
            }

            response = f"📊 Resumen de gastos {period_names.get(period, period)}:\n\n"
            for cat in by_category[:5]:
                name = cat.get("category_name", "")
                amount = float(cat.get("total", 0) or 0)
                pct = float(cat.get("percentage", 0) or 0)
                response += f"• {name}: ${amount:,.0f} ({pct:.0f}%)\n"

            response += f"\n💰 Total: ${total:,.0f}"
            return response

        elif tool_name == "consultar_presupuesto":
            budgets = data.get("budgets", [])
            if not budgets:
                return "📋 No tenés presupuestos configurados."

            response = "📋 Estado de tus presupuestos:\n\n"
            for b in budgets:
                name = b.get("category", "")
                limit = float(b.get("limit", 0) or 0)
                spent = float(b.get("spent", 0) or 0)
                remaining = float(b.get("remaining", 0) or 0)
                pct = float(b.get("percentage", 0) or 0)

                status = "✓" if pct < 80 else "⚠️" if pct < 100 else "🔴"
                response += f"• {name}: ${limit:,.0f}/mes\n"
                response += f"  └ Gastaste ${spent:,.0f} - te quedan ${remaining:,.0f} {status} ({pct:.0f}%)\n\n"

            return response.strip()

        elif tool_name == "eliminar_gasto":
            return data.get("message") or "🗑️ Gasto eliminado."

        elif tool_name == "eliminar_gasto_masivo":
            count = data.get("deleted_count", 0)
            return f"🗑️ Se eliminaron {count} gasto(s)."

        elif tool_name == "modificar_gasto":
            return data.get("message") or "✏️ Gasto modificado."

        elif tool_name == "fijar_presupuesto":
            message = data.get("message", "")
            if message:
                return message
            budget = data.get("budget", {})
            category = budget.get("category", "")
            limit = float(budget.get("monthly_limit", 0) or 0)
            created = data.get("created", False)
            action = "creado" if created else "actualizado"
            return f"💰 Presupuesto {action}: {category} con ${limit:,.0f}/mes"

        elif tool_name == "eliminar_presupuesto":
            return data.get("message") or "🧹 Presupuesto eliminado."

        elif tool_name == "listar_categorias":
            categories = data.get("categories", [])
            if not categories:
                return "📋 No hay categorías disponibles."
            names = ", ".join(str(c.get("name", "")) for c in categories[:15] if c.get("name"))
            return f"📋 Tus categorías: {names}"

        elif tool_name == "crear_categoria":
            return data.get("message") or "✅ Categoría creada."

        elif tool_name == "editar_categoria":
            return data.get("message") or "✏️ Categoría actualizada."

        elif tool_name == "eliminar_categoria":
            return data.get("message") or "🗑️ Categoría eliminada."

        elif tool_name == "registrar_ingreso":
            return data.get("message") or f"✅ Ingreso de ${args.get('amount', 0):,.0f} registrado."

        elif tool_name == "eliminar_ingreso":
            return data.get("message") or "🗑️ Ingreso eliminado."

        elif tool_name == "modificar_ingreso":
            return data.get("message") or "✏️ Ingreso modificado."

        return "✓ Operación completada."
