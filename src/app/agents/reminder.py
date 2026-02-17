"""Reminder Agent - Reminder and alert management."""

import json
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog
from openai import AsyncOpenAI

from ..config import get_settings
from ..config.database import get_pool
from .base import AgentResult, BaseAgent

logger = structlog.get_logger()


class ReminderAgent(BaseAgent):
    """Agent for managing reminders."""

    name = "reminder"

    def __init__(self):
        """Initialize the reminder agent."""
        super().__init__()
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    async def process(
        self,
        message: str,
        phone: str,
        tenant_id: str,
        history: list,
        **kwargs,
    ) -> AgentResult:
        """Process a reminder-related message.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.

        Returns:
            The agent's response.
        """
        logger.info("Reminder agent processing", message=message[:50])

        prompt = await self.get_prompt(tenant_id)

        # Build messages
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": f"Fecha y hora actual: {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
        ]

        # Add history context
        for msg in history[-6:]:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": message})

        # Define tools
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "crear_recordatorio",
                    "description": "Crea un nuevo recordatorio",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "Qué recordar"},
                            "trigger_date": {"type": "string", "description": "Fecha YYYY-MM-DD"},
                            "trigger_time": {"type": "string", "description": "Hora HH:MM"},
                            "recurrence": {
                                "type": "string",
                                "enum": ["none", "daily", "weekly", "monthly"],
                                "description": "Frecuencia de repetición",
                            },
                        },
                        "required": ["message"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "listar_recordatorios",
                    "description": "Lista recordatorios pendientes",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "search": {"type": "string", "description": "Buscar por texto"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "eliminar_recordatorio",
                    "description": "Elimina un recordatorio",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "search_query": {"type": "string", "description": "Texto para buscar"},
                        },
                        "required": ["search_query"],
                    },
                },
            },
        ]

        try:
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

                logger.info(f"Reminder tool call: {tool_name}", args=tool_args)

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
            logger.error("Reminder agent error", error=str(e))
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
        """Execute a reminder tool.

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

            if tool_name == "crear_recordatorio":
                message = args.get("message", "")
                trigger_date = args.get("trigger_date")
                trigger_time = args.get("trigger_time", "09:00")
                recurrence = args.get("recurrence", "none")

                # Default to tomorrow if no date
                if not trigger_date:
                    trigger_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

                query = """
                    INSERT INTO reminders (
                        tenant_id, user_phone, message, trigger_date, trigger_time, recurrence
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                """
                row = await pool.fetchrow(
                    query, tenant_id, phone, message, trigger_date, trigger_time, recurrence
                )
                return {
                    "success": True,
                    "data": {
                        "id": str(row["id"]),
                        "message": message,
                        "trigger_date": trigger_date,
                        "trigger_time": trigger_time,
                        "recurrence": recurrence,
                    },
                }

            elif tool_name == "listar_recordatorios":
                search = args.get("search", "")
                
                query = """
                    SELECT id, message, trigger_date, trigger_time, recurrence
                    FROM reminders
                    WHERE tenant_id = $1 AND user_phone = $2
                      AND (trigger_date >= CURRENT_DATE OR recurrence != 'none')
                """
                params = [tenant_id, phone]
                
                if search:
                    query += " AND message ILIKE $3"
                    params.append(f"%{search}%")
                
                query += " ORDER BY trigger_date, trigger_time LIMIT 20"
                
                rows = await pool.fetch(query, *params)
                reminders = [
                    {
                        "id": str(row["id"]),
                        "message": row["message"],
                        "trigger_date": row["trigger_date"].strftime("%Y-%m-%d") if row["trigger_date"] else None,
                        "trigger_time": row["trigger_time"],
                        "recurrence": row["recurrence"],
                    }
                    for row in rows
                ]
                return {"success": True, "data": {"reminders": reminders}}

            elif tool_name == "eliminar_recordatorio":
                search_query = args.get("search_query", "")
                
                # Find matching reminder
                query = """
                    DELETE FROM reminders
                    WHERE tenant_id = $1 AND user_phone = $2 AND message ILIKE $3
                    RETURNING id, message
                """
                row = await pool.fetchrow(query, tenant_id, phone, f"%{search_query}%")
                
                if row:
                    return {
                        "success": True,
                        "data": {"deleted": True, "message": row["message"]},
                    }
                else:
                    return {"success": True, "data": {"deleted": False}}

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
            logger.error("Reminder tool failed", error=result.get("error"), tool=tool_name)
            return "Hubo un problema procesando tu solicitud. Intentá de nuevo."

        data = result.get("data", {})

        if tool_name == "crear_recordatorio":
            message = data.get("message", "")
            date = data.get("trigger_date", "")
            time = data.get("trigger_time", "")
            recurrence = data.get("recurrence", "none")

            response = f"⏰ Recordatorio creado:\n\"{message.capitalize()}\"\n📆 {self._format_date(date)} a las {time}"
            
            if recurrence != "none":
                recurrence_text = {
                    "daily": "Todos los días",
                    "weekly": "Todas las semanas",
                    "monthly": "Todos los meses",
                }
                response += f"\n🔄 {recurrence_text.get(recurrence, recurrence)}"
            
            return response

        elif tool_name == "listar_recordatorios":
            reminders = data.get("reminders", [])
            if not reminders:
                return "⏰ No tenés recordatorios pendientes.\n\n¿Querés que te recuerde algo?"

            response = "⏰ Tus recordatorios pendientes:\n\n"
            
            # Group by date
            today = datetime.now().date()
            today_items = []
            tomorrow_items = []
            later_items = []
            recurring_items = []
            
            for r in reminders:
                if r.get("recurrence") != "none":
                    recurring_items.append(r)
                else:
                    date = datetime.strptime(r["trigger_date"], "%Y-%m-%d").date()
                    if date == today:
                        today_items.append(r)
                    elif date == today + timedelta(days=1):
                        tomorrow_items.append(r)
                    else:
                        later_items.append(r)
            
            if today_items:
                response += "📌 Hoy:\n"
                for r in today_items:
                    response += f"• {r['trigger_time']} - {r['message']}\n"
                response += "\n"
            
            if tomorrow_items:
                response += "📌 Mañana:\n"
                for r in tomorrow_items:
                    response += f"• {r['trigger_time']} - {r['message']}\n"
                response += "\n"
            
            if later_items:
                response += "📌 Próximos:\n"
                for r in later_items[:5]:
                    response += f"• {self._format_date(r['trigger_date'])} {r['trigger_time']} - {r['message']}\n"
                response += "\n"
            
            if recurring_items:
                response += "📌 Recurrentes:\n"
                for r in recurring_items:
                    recurrence_text = {
                        "daily": "diario",
                        "weekly": "semanal",
                        "monthly": "mensual",
                    }
                    response += f"• \"{r['message']}\" - {recurrence_text.get(r['recurrence'], r['recurrence'])}\n"

            return response.strip()

        elif tool_name == "eliminar_recordatorio":
            deleted = data.get("deleted", False)
            if deleted:
                message = data.get("message", "")
                return f"✅ Recordatorio cancelado:\n\"{message}\""
            else:
                return "❌ No encontré un recordatorio que coincida."

        return "✓ Operación completada."

    def _format_date(self, date_str: str) -> str:
        """Format a date string for display."""
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
            today = datetime.now().date()

            if date.date() == today:
                return "Hoy"
            elif date.date() == today + timedelta(days=1):
                return "Mañana"
            else:
                days = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
                return f"{days[date.weekday()]} {date.day}/{date.month}"
        except Exception:
            return date_str
