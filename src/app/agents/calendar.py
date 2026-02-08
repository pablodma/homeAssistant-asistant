"""Calendar Agent - Event and schedule management."""

import json
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
import structlog
from openai import AsyncOpenAI

from ..config import get_settings
from .base import AgentResult, BaseAgent

logger = structlog.get_logger()


class CalendarAgent(BaseAgent):
    """Agent for managing calendar events."""

    name = "calendar"

    def __init__(self):
        """Initialize the calendar agent."""
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
        """Process a calendar-related message.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.

        Returns:
            The agent's response.
        """
        logger.info("Calendar agent processing", message=message[:50])

        prompt = await self.get_prompt(tenant_id)

        # Build messages
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": f"Fecha actual: {datetime.now().strftime('%Y-%m-%d %H:%M')}. Usuario: {phone}"},
        ]

        # Add history context
        for msg in history[-4:]:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": message})

        # Define tools
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "crear_evento",
                    "description": "Crea un nuevo evento en el calendario",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Título del evento"},
                            "date": {"type": "string", "description": "Fecha YYYY-MM-DD"},
                            "time": {"type": "string", "description": "Hora HH:MM (24h)"},
                            "duration_minutes": {"type": "integer", "description": "Duración en minutos"},
                            "location": {"type": "string", "description": "Ubicación"},
                            "description": {"type": "string", "description": "Descripción"},
                        },
                        "required": ["title", "date"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "listar_eventos",
                    "description": "Lista eventos del calendario",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "Fecha específica YYYY-MM-DD"},
                            "start_date": {"type": "string", "description": "Inicio del rango"},
                            "end_date": {"type": "string", "description": "Fin del rango"},
                            "search": {"type": "string", "description": "Buscar por texto"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "modificar_evento",
                    "description": "Modifica un evento existente",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "search_query": {"type": "string", "description": "Texto para buscar el evento"},
                            "title": {"type": "string", "description": "Nuevo título"},
                            "date": {"type": "string", "description": "Nueva fecha"},
                            "time": {"type": "string", "description": "Nueva hora"},
                            "location": {"type": "string", "description": "Nueva ubicación"},
                        },
                        "required": ["search_query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "eliminar_evento",
                    "description": "Elimina un evento",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "search_query": {"type": "string", "description": "Texto para buscar"},
                            "date": {"type": "string", "description": "Fecha para filtrar"},
                        },
                        "required": ["search_query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "verificar_disponibilidad",
                    "description": "Verifica si un horario está libre",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "Fecha YYYY-MM-DD"},
                            "time": {"type": "string", "description": "Hora HH:MM"},
                            "duration": {"type": "integer", "description": "Duración en minutos"},
                        },
                        "required": ["date", "time"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "estado_google",
                    "description": "Verifica estado de conexión con Google Calendar",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "proximo_evento",
                    "description": "Obtiene el próximo evento programado",
                    "parameters": {
                        "type": "object",
                        "properties": {},
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
                max_tokens=1000,
                temperature=0.3,
            )

            choice = response.choices[0]
            tokens_in = response.usage.prompt_tokens if response.usage else None
            tokens_out = response.usage.completion_tokens if response.usage else None

            # Check if tool was called
            if choice.message.tool_calls:
                tool_call = choice.message.tool_calls[0]
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                logger.info(f"Calendar tool call: {tool_name}", args=tool_args)

                # Execute the tool
                tool_result = await self._execute_tool(tool_name, tool_args, tenant_id, phone)

                # Generate response based on tool result
                response_text = await self._generate_response(
                    tool_name, tool_args, tool_result, message
                )

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
            logger.error("Calendar agent error", error=str(e))
            return AgentResult(
                response="Hubo un problema procesando tu solicitud de calendario.",
                agent_used=self.name,
            )

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        tenant_id: str,
        phone: str,
    ) -> dict[str, Any]:
        """Execute a calendar tool by calling the backend API.

        Args:
            tool_name: Name of the tool.
            args: Tool arguments.
            tenant_id: The tenant ID.
            phone: User's phone number.

        Returns:
            Tool execution result.
        """
        base_url = f"{self.settings.backend_api_url}/api/v1/tenants/{tenant_id}"
        headers = {"Authorization": f"Bearer {self.settings.backend_api_key}"}

        async with httpx.AsyncClient() as client:
            try:
                if tool_name == "crear_evento":
                    args["user_phone"] = phone
                    response = await client.post(
                        f"{base_url}/agent/calendar/event",
                        json=args,
                        headers=headers,
                        timeout=30.0,
                    )
                elif tool_name == "listar_eventos":
                    params = {k: v for k, v in args.items() if v}
                    params["user_phone"] = phone
                    response = await client.get(
                        f"{base_url}/agent/calendar/events",
                        params=params,
                        headers=headers,
                        timeout=30.0,
                    )
                elif tool_name == "modificar_evento":
                    response = await client.put(
                        f"{base_url}/agent/calendar/event/search",
                        json=args,
                        headers=headers,
                        timeout=30.0,
                    )
                elif tool_name == "eliminar_evento":
                    response = await client.delete(
                        f"{base_url}/agent/calendar/event/search",
                        params=args,
                        headers=headers,
                        timeout=30.0,
                    )
                elif tool_name == "verificar_disponibilidad":
                    args["user_phone"] = phone
                    response = await client.get(
                        f"{base_url}/agent/calendar/availability",
                        params=args,
                        headers=headers,
                        timeout=30.0,
                    )
                elif tool_name == "estado_google":
                    response = await client.get(
                        f"{base_url}/agent/calendar/connection-status",
                        params={"user_phone": phone},
                        headers=headers,
                        timeout=30.0,
                    )
                elif tool_name == "proximo_evento":
                    response = await client.get(
                        f"{base_url}/agent/calendar/next",
                        params={"user_phone": phone},
                        headers=headers,
                        timeout=30.0,
                    )
                else:
                    return {"success": False, "error": f"Unknown tool: {tool_name}"}

                if response.status_code == 200:
                    return {"success": True, "data": response.json()}
                else:
                    return {
                        "success": False,
                        "error": response.text,
                        "status_code": response.status_code,
                    }

            except Exception as e:
                logger.error(f"Tool execution failed: {tool_name}", error=str(e))
                return {"success": False, "error": str(e)}

    async def _generate_response(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        original_message: str,
    ) -> str:
        """Generate a user-friendly response from tool result.

        Args:
            tool_name: Name of the tool.
            args: Tool arguments.
            result: Tool execution result.
            original_message: The original user message.

        Returns:
            Formatted response.
        """
        if not result.get("success"):
            error = result.get("error", "Error desconocido")
            return f"❌ No pude completar la operación: {error}"

        data = result.get("data", {})

        if tool_name == "crear_evento":
            title = args.get("title", "")
            date = args.get("date", "")
            time = args.get("time", "09:00")
            location = args.get("location", "")

            response = f"📅 Evento creado:\n\"{title}\"\n📆 {self._format_date(date)} a las {time}"
            if location:
                response += f"\n📍 {location}"
            response += "\n⏱️ Duración: 1 hora"
            return response

        elif tool_name == "listar_eventos":
            events = data.get("events", [])
            if not events:
                return "📅 No tenés eventos programados para ese período."

            response = "📅 Tus eventos:\n\n"
            for event in events[:10]:
                title = event.get("title", "")
                date = event.get("date", "")
                time = event.get("time", "")
                location = event.get("location", "")

                response += f"• {time} - {title}\n"
                if location:
                    response += f"  📍 {location}\n"

            return response.strip()

        elif tool_name == "modificar_evento":
            modified = data.get("modified", False)
            if modified:
                title = data.get("title", "")
                changes = data.get("changes", [])
                response = f"✏️ Evento modificado:\n\"{title}\"\n\nCambios:\n"
                for change in changes:
                    response += f"• {change}\n"
                return response.strip()
            else:
                return "❌ No encontré el evento para modificar."

        elif tool_name == "eliminar_evento":
            deleted = data.get("deleted", False)
            if deleted:
                title = data.get("title", "")
                return f"✅ Evento cancelado:\n\"{title}\""
            else:
                return "❌ No encontré el evento para eliminar."

        elif tool_name == "verificar_disponibilidad":
            available = data.get("available", False)
            if available:
                date = args.get("date", "")
                time = args.get("time", "")
                return f"✅ Tenés libre el {self._format_date(date)} a las {time}."
            else:
                conflict = data.get("conflict", {})
                suggestions = data.get("suggestions", [])
                response = f"⚠️ A esa hora tenés:\n\"{conflict.get('title', '')}\"\n\n"
                if suggestions:
                    response += "Horarios sugeridos:\n"
                    for s in suggestions[:3]:
                        response += f"• {s}\n"
                return response.strip()

        elif tool_name == "estado_google":
            connected = data.get("connected", False)
            if connected:
                return "✅ Tu Google Calendar está conectado y sincronizado."
            else:
                auth_url = data.get("auth_url", "")
                return f"📅 Para sincronizar tus eventos con Google Calendar, conectá tu cuenta:\n\n👉 {auth_url}\n\nTocá el link, autorizá con tu cuenta de Google y listo."

        elif tool_name == "proximo_evento":
            event = data.get("event")
            if event:
                title = event.get("title", "")
                date = event.get("date", "")
                time = event.get("time", "")
                location = event.get("location", "")

                response = f"📅 Tu próximo evento:\n\"{title}\"\n📆 {self._format_date(date)} a las {time}"
                if location:
                    response += f"\n📍 {location}"
                return response
            else:
                return "📅 No tenés eventos próximos programados."

        return "✓ Operación completada."

    def _format_date(self, date_str: str) -> str:
        """Format a date string for display.

        Args:
            date_str: Date in YYYY-MM-DD format.

        Returns:
            Formatted date string.
        """
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
            today = datetime.now().date()

            if date.date() == today:
                return "Hoy"
            elif date.date() == today + timedelta(days=1):
                return "Mañana"
            elif date.date() == today + timedelta(days=2):
                return "Pasado mañana"
            else:
                days = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
                months = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
                day_name = days[date.weekday()]
                return f"{day_name} {date.day} de {months[date.month - 1]}"
        except Exception:
            return date_str
