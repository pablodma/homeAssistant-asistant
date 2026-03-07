"""Shopping Agent - Shopping list management."""

import json
from datetime import datetime
from typing import Any, Optional

import structlog

from ..config import get_settings
from ..config.database import get_pool
from .base import (
    FIRST_TIME_TOOL_DEFINITION,
    FIRST_TIME_TOOL_DEFINITION_ANTHROPIC,
    AgentResult,
    BaseAgent,
    openai_tool_to_anthropic,
)

logger = structlog.get_logger()


class ShoppingAgent(BaseAgent):
    """Agent for managing shopping lists."""

    name = "shopping"

    def __init__(self):
        """Initialize the shopping agent."""
        super().__init__()
        self._init_llm_client("shopping_model_provider")

    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs,
    ) -> AgentResult:
        """Process a shopping-related message.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.

        Returns:
            The agent's response.
        """
        logger.info("Shopping agent processing", message=message[:50])

        prompt = await self.get_prompt(tenant_id)

        # Build messages
        messages = [
            {"role": "system", "content": prompt},
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
                    "name": "agregar_item",
                    "description": "Agrega un item a la lista de compras",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string", "description": "Nombre del item"},
                            "quantity": {"type": "number", "description": "Cantidad"},
                            "unit": {"type": "string", "description": "Unidad (kg, l, unidades)"},
                            "list_name": {"type": "string", "description": "Nombre de la lista"},
                        },
                        "required": ["item_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ver_lista",
                    "description": "Ver items de una lista de compras",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "list_name": {"type": "string", "description": "Nombre de la lista"},
                            "show_purchased": {"type": "boolean", "description": "Incluir comprados"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "marcar_comprado",
                    "description": "Marca un item como comprado",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string", "description": "Nombre del item"},
                            "list_name": {"type": "string", "description": "Nombre de la lista"},
                        },
                        "required": ["item_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "eliminar_item",
                    "description": "Elimina un item de la lista",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string", "description": "Nombre del item"},
                            "list_name": {"type": "string", "description": "Nombre de la lista"},
                        },
                        "required": ["item_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "limpiar_lista",
                    "description": "Elimina todos los items comprados de la lista",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "list_name": {"type": "string", "description": "Nombre de la lista"},
                        },
                    },
                },
            },
        ]

        try:
            if self.provider == "anthropic":
                # --- Anthropic path ---
                anthropic_tools = [openai_tool_to_anthropic(t) for t in tools]
                if is_first_time:
                    anthropic_tools.append(FIRST_TIME_TOOL_DEFINITION_ANTHROPIC)

                system_text, filtered_msgs = self._extract_system_and_messages(messages)

                response = await self.client.messages.create(
                    model=self.settings.anthropic_subagent_model,
                    system=system_text,
                    messages=filtered_msgs,
                    tools=anthropic_tools,
                    tool_choice={"type": "auto"},
                    max_tokens=1000,
                )

                tokens_in, tokens_out = self._anthropic_tokens(response)

                # Check if tool was called
                tool_info = self._extract_tool_use(response)
                if tool_info:
                    tool_name, tool_args, tool_use_id = tool_info

                    logger.info(f"Shopping tool call: {tool_name}", args=tool_args)

                    # First-time completion: execute and let LLM generate follow-up
                    if tool_name == "completar_configuracion_inicial":
                        result_msg = await self.complete_first_time(phone)
                        filtered_msgs.append({"role": "assistant", "content": response.content})
                        filtered_msgs.append(self._build_tool_result_msg(tool_use_id, result_msg))
                        follow_up = await self.client.messages.create(
                            model=self.settings.anthropic_subagent_model,
                            system=system_text,
                            messages=filtered_msgs,
                            max_tokens=500,
                        )
                        return AgentResult(
                            response=self._extract_text(follow_up) or "¡Configuración completada!",
                            agent_used=self.name,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                        )

                    # Execute the tool
                    tool_result = await self._execute_tool(tool_name, tool_args, tenant_id, phone)

                    # Generate response based on tool result
                    response_text = self._generate_response(tool_name, tool_args, tool_result)

                    return AgentResult(
                        response=response_text,
                        agent_used=self.name,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        metadata={"tool": tool_name, "result": tool_result},
                    )

                # Direct response
                return AgentResult(
                    response=self._extract_text(response) or "No pude procesar tu solicitud.",
                    agent_used=self.name,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

            else:
                # --- OpenAI path (rollback) ---
                if is_first_time:
                    tools.append(FIRST_TIME_TOOL_DEFINITION)

                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.4,
                    max_completion_tokens=1000,
                )

                choice = response.choices[0]
                tokens_in = response.usage.prompt_tokens if response.usage else None
                tokens_out = response.usage.completion_tokens if response.usage else None

                # Check if tool was called
                if choice.message.tool_calls:
                    tool_call = choice.message.tool_calls[0]
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    logger.info(f"Shopping tool call: {tool_name}", args=tool_args)

                    # First-time completion: execute and let LLM generate follow-up
                    if tool_name == "completar_configuracion_inicial":
                        result_msg = await self.complete_first_time(phone)
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{"id": tool_call.id, "type": "function", "function": {"name": tool_name, "arguments": tool_call.function.arguments}}],
                        })
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_msg})
                        follow_up = await self.client.chat.completions.create(
                            model=self.settings.openai_model,
                            messages=messages,
                            temperature=0.4,
                            max_completion_tokens=500,
                        )
                        return AgentResult(
                            response=follow_up.choices[0].message.content or "¡Configuración completada!",
                            agent_used=self.name,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                        )

                    # Execute the tool
                    tool_result = await self._execute_tool(tool_name, tool_args, tenant_id, phone)

                    # Generate response based on tool result
                    response_text = self._generate_response(tool_name, tool_args, tool_result)

                    return AgentResult(
                        response=response_text,
                        agent_used=self.name,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        metadata={"tool": tool_name, "result": tool_result},
                    )

                # Direct response
                return AgentResult(
                    response=choice.message.content or "No pude procesar tu solicitud.",
                    agent_used=self.name,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

        except Exception as e:
            logger.error("Shopping agent error", error=str(e))
            return AgentResult(
                response="Hubo un problema procesando tu solicitud. Intentá de nuevo.",
                agent_used=self.name,
            )

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        tenant_id: str,
        phone: str,
    ) -> dict[str, Any]:
        """Execute a shopping tool.

        Args:
            tool_name: Name of the tool.
            args: Tool arguments.
            tenant_id: The tenant ID.
            phone: User's phone number.

        Returns:
            Tool execution result.
        """
        try:
            pool = await get_pool()
            list_name = args.get("list_name", "Supermercado")

            if tool_name == "agregar_item":
                item_name = args.get("item_name", "")
                quantity = args.get("quantity", 1)
                unit = args.get("unit", "")

                # Check if item already exists
                check_query = """
                    SELECT id, quantity FROM shopping_items
                    WHERE tenant_id = $1 AND list_name = $2 AND item_name ILIKE $3 AND is_purchased = false
                """
                existing = await pool.fetchrow(check_query, tenant_id, list_name, item_name)

                if existing:
                    # Update quantity
                    update_query = """
                        UPDATE shopping_items SET quantity = quantity + $1, updated_at = NOW()
                        WHERE id = $2
                    """
                    await pool.execute(update_query, quantity, existing["id"])
                    return {
                        "success": True,
                        "data": {
                            "item_name": item_name,
                            "quantity": existing["quantity"] + quantity,
                            "list_name": list_name,
                            "updated": True,
                        },
                    }
                else:
                    # Insert new item
                    insert_query = """
                        INSERT INTO shopping_items (tenant_id, user_phone, list_name, item_name, quantity, unit)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        RETURNING id
                    """
                    await pool.execute(insert_query, tenant_id, phone, list_name, item_name, quantity, unit)
                    return {
                        "success": True,
                        "data": {
                            "item_name": item_name,
                            "quantity": quantity,
                            "unit": unit,
                            "list_name": list_name,
                        },
                    }

            elif tool_name == "ver_lista":
                show_purchased = args.get("show_purchased", False)

                query = """
                    SELECT item_name, quantity, unit, is_purchased
                    FROM shopping_items
                    WHERE tenant_id = $1 AND list_name = $2
                """
                if not show_purchased:
                    query += " AND is_purchased = false"
                query += " ORDER BY is_purchased, created_at"

                rows = await pool.fetch(query, tenant_id, list_name)
                items = [
                    {
                        "item_name": row["item_name"],
                        "quantity": row["quantity"],
                        "unit": row["unit"],
                        "is_purchased": row["is_purchased"],
                    }
                    for row in rows
                ]
                return {"success": True, "data": {"items": items, "list_name": list_name}}

            elif tool_name == "marcar_comprado":
                item_name = args.get("item_name", "")

                update_query = """
                    UPDATE shopping_items SET is_purchased = true, updated_at = NOW()
                    WHERE tenant_id = $1 AND list_name = $2 AND item_name ILIKE $3 AND is_purchased = false
                    RETURNING item_name
                """
                row = await pool.fetchrow(update_query, tenant_id, list_name, f"%{item_name}%")

                if row:
                    return {"success": True, "data": {"marked": True, "item_name": row["item_name"]}}
                else:
                    return {"success": True, "data": {"marked": False}}

            elif tool_name == "eliminar_item":
                item_name = args.get("item_name", "")

                delete_query = """
                    DELETE FROM shopping_items
                    WHERE tenant_id = $1 AND list_name = $2 AND item_name ILIKE $3
                    RETURNING item_name
                """
                row = await pool.fetchrow(delete_query, tenant_id, list_name, f"%{item_name}%")

                if row:
                    return {"success": True, "data": {"deleted": True, "item_name": row["item_name"]}}
                else:
                    return {"success": True, "data": {"deleted": False}}

            elif tool_name == "limpiar_lista":
                delete_query = """
                    DELETE FROM shopping_items
                    WHERE tenant_id = $1 AND list_name = $2 AND is_purchased = true
                """
                result = await pool.execute(delete_query, tenant_id, list_name)
                count = int(result.split()[-1]) if result else 0
                return {"success": True, "data": {"cleared": count, "list_name": list_name}}

            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name}", error=str(e))
            return {"success": False, "error": str(e)}

    def _generate_response(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        """Generate a user-friendly response.

        Args:
            tool_name: Name of the tool.
            args: Tool arguments.
            result: Tool execution result.

        Returns:
            Formatted response.
        """
        if not result.get("success"):
            logger.error("Shopping tool failed", error=result.get("error"), tool=tool_name)
            return "Hubo un problema procesando tu solicitud. Intentá de nuevo."

        data = result.get("data", {})

        if tool_name == "agregar_item":
            item = data.get("item_name", "")
            quantity = data.get("quantity", 1)
            unit = data.get("unit", "")
            list_name = data.get("list_name", "Supermercado")
            updated = data.get("updated", False)

            qty_str = f"{quantity} {unit}".strip() if quantity > 1 or unit else ""
            if updated:
                return f"✅ Actualizado: {item} {qty_str} en lista {list_name}"
            else:
                return f"✅ Agregado: {item} {qty_str} a lista {list_name}"

        elif tool_name == "ver_lista":
            items = data.get("items", [])
            list_name = data.get("list_name", "Supermercado")

            if not items:
                return f"🛒 La lista {list_name} está vacía.\n\n¿Querés agregar algo?"

            pending = [i for i in items if not i.get("is_purchased")]
            purchased = [i for i in items if i.get("is_purchased")]

            response = f"🛒 Lista {list_name} ({len(pending)} items):\n\n"

            for item in pending:
                name = item["item_name"]
                qty = item.get("quantity", 1)
                unit = item.get("unit", "")
                qty_str = f" ({qty} {unit})" if qty > 1 or unit else ""
                response += f"• {name}{qty_str}\n"

            if purchased:
                response += f"\n✅ Comprados ({len(purchased)}):\n"
                for item in purchased[:5]:
                    response += f"• ~~{item['item_name']}~~\n"

            return response.strip()

        elif tool_name == "marcar_comprado":
            marked = data.get("marked", False)
            item = data.get("item_name", "")
            if marked:
                return f"✅ Marcado como comprado: {item}"
            else:
                return "❌ No encontré ese item en la lista."

        elif tool_name == "eliminar_item":
            deleted = data.get("deleted", False)
            item = data.get("item_name", "")
            if deleted:
                return f"🗑️ Eliminado: {item}"
            else:
                return "❌ No encontré ese item en la lista."

        elif tool_name == "limpiar_lista":
            cleared = data.get("cleared", 0)
            list_name = data.get("list_name", "Supermercado")
            if cleared > 0:
                return f"🗑️ Se eliminaron {cleared} item(s) comprados de {list_name}"
            else:
                return f"La lista {list_name} no tenía items comprados para limpiar."

        return "✓ Operación completada."
