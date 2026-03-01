"""Agenda Agent - Event, schedule, and reminder management."""

import json
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog
from openai import AsyncOpenAI

from ..config import get_settings
from ..services.backend_client import get_backend_client
from .base import FIRST_TIME_TOOL_DEFINITION, AgentResult, BaseAgent

logger = structlog.get_logger()


class CalendarAgent(BaseAgent):
    """Agent for managing calendar events and reminders (unified agenda)."""

    name = "agenda"

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
        now = datetime.now()
        day_name = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'][now.weekday()]
        date_context = f"Hoy es {day_name} {now.strftime('%Y-%m-%d %H:%M')}. Usuario: {phone}"
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": date_context},
        ]

        # Add history context
        for msg in history[-6:]:
            messages.append({"role": msg.role, "content": msg.content})

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

        if is_first_time:
            tools.append(FIRST_TIME_TOOL_DEFINITION)

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

                logger.info(f"Calendar tool call: {tool_name}", args=tool_args)

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
                response_text = await self._generate_response(
                    tool_name, tool_args, tool_result, message,
                    tenant_id=tenant_id, phone=phone,
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
        """Execute a calendar tool by calling the backend API.

        Args:
            tool_name: Name of the tool.
            args: Tool arguments.
            tenant_id: The tenant ID.
            phone: User's phone number.

        Returns:
            Tool execution result.
        """
        base_path = f"/api/v1/tenants/{tenant_id}"
        backend = get_backend_client()

        try:
            if tool_name == "crear_evento":
                payload = {
                    "title": args["title"],
                    "event_date": args.get("date"),
                    "start_time": args.get("time"),
                    "duration_minutes": args.get("duration_minutes", 60),
                    "location": args.get("location"),
                    "description": args.get("description"),
                    "user_phone": phone,
                }
                payload = {k: v for k, v in payload.items() if v is not None}
                response = await backend.post(
                    f"{base_path}/agent/calendar/event", json=payload,
                )
            elif tool_name == "listar_eventos":
                params = {k: v for k, v in args.items() if v}
                params["user_phone"] = phone
                response = await backend.get(
                    f"{base_path}/agent/calendar/events", params=params,
                )
            elif tool_name == "modificar_evento":
                payload = {"search_query": args.get("search_query")}
                if args.get("title"):
                    payload["title"] = args["title"]
                if args.get("date"):
                    payload["event_date"] = args["date"]
                if args.get("time"):
                    payload["start_time"] = args["time"]
                if args.get("location"):
                    payload["location"] = args["location"]
                response = await backend.put(
                    f"{base_path}/agent/calendar/event/search",
                    json=payload, params={"user_phone": phone},
                )
            elif tool_name == "eliminar_evento":
                payload = {"search_query": args.get("search_query")}
                if args.get("date"):
                    payload["event_date"] = args["date"]
                response = await backend.request(
                    "DELETE",
                    f"{base_path}/agent/calendar/event/search",
                    json=payload, params={"user_phone": phone},
                )
            elif tool_name == "verificar_disponibilidad":
                args["user_phone"] = phone
                response = await backend.get(
                    f"{base_path}/agent/calendar/availability", params=args,
                )
            elif tool_name == "estado_google":
                response = await backend.get(
                    f"{base_path}/agent/calendar/connection-status",
                    params={"user_phone": phone},
                )
            elif tool_name == "proximo_evento":
                response = await backend.get(
                    f"{base_path}/agent/calendar/next",
                    params={"user_phone": phone},
                )
            elif tool_name == "crear_recordatorio":
                trigger_date = args.get("trigger_date")
                if not trigger_date:
                    trigger_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                params = {
                    "message": args.get("message", ""),
                    "trigger_date": trigger_date,
                    "trigger_time": args.get("trigger_time", "09:00"),
                    "recurrence": args.get("recurrence", "none"),
                    "user_phone": phone,
                }
                response = await backend.post(
                    f"{base_path}/agent/reminders", params=params,
                )
            elif tool_name == "listar_recordatorios":
                params = {"user_phone": phone}
                if args.get("search"):
                    params["search"] = args["search"]
                response = await backend.get(
                    f"{base_path}/agent/reminders", params=params,
                )
            elif tool_name == "eliminar_recordatorio":
                params = {
                    "search_query": args.get("search_query", ""),
                    "user_phone": phone,
                }
                response = await backend.request(
                    "DELETE",
                    f"{base_path}/agent/reminders/search",
                    params=params,
                )
            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}

            if response.status_code in (200, 201):
                return {"success": True, "data": response.json()}
            elif response.status_code == 204:
                return {"success": True, "data": {}}
            else:
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code,
                }

        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name}", error=str(e))
            return {"success": False, "error": str(e)}

    def _parse_event_datetime(self, event: dict[str, Any]) -> tuple[str, str]:
        """Extract date and time from an event's start_datetime field.

        Args:
            event: Event dict from backend API.

        Returns:
            Tuple of (date_str YYYY-MM-DD, time_str HH:MM).
        """
        start_dt = event.get("start_datetime", "")
        if not start_dt:
            return ("", "")
        try:
            if isinstance(start_dt, str):
                dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
            else:
                dt = start_dt
            return (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"))
        except Exception:
            return (str(start_dt)[:10], "")

    def _format_event_line(self, event: dict[str, Any]) -> str:
        """Format a single event as a display line.

        Args:
            event: Event dict from backend API.

        Returns:
            Formatted event line.
        """
        title = event.get("title", "")
        _, time_str = self._parse_event_datetime(event)
        location = event.get("location", "")
        line = f"• {time_str} - {title}" if time_str else f"• {title}"
        if location:
            line += f"\n  📍 {location}"
        return line

    async def _get_google_connect_tip(self, tenant_id: str, phone: str) -> str | None:
        """Check Google Calendar connection and return a tip with auth URL if not connected."""
        try:
            backend = get_backend_client()
            response = await backend.get(
                f"/api/v1/tenants/{tenant_id}/agent/calendar/connection-status",
                timeout=10.0,
                params={"user_phone": phone},
            )
            if response.status_code == 200:
                data = response.json()
                if not data.get("connected") and data.get("auth_url"):
                    return f"💡 Sincronizá con Google Calendar para verlo en tu agenda:\n👉 {data['auth_url']}"
        except Exception:
            pass
        return None

    async def _generate_response(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        original_message: str,
        tenant_id: str = "",
        phone: str = "",
    ) -> str:
        """Generate a user-friendly response from tool result.

        Args:
            tool_name: Name of the tool.
            args: Tool arguments.
            result: Tool execution result.
            original_message: The original user message.
            tenant_id: The tenant ID (for Google Calendar check).
            phone: User's phone number (for Google Calendar check).

        Returns:
            Formatted response.
        """
        if not result.get("success"):
            error = result.get("error", "Error desconocido")
            return f"❌ No pude completar la operación: {error}"

        data = result.get("data", {})

        if tool_name == "crear_evento":
            event = data.get("event") or {}
            created = data.get("created", True)

            if not created and data.get("duplicate_warning"):
                warning = data["duplicate_warning"]
                return f"⚠️ {warning.get('message', 'Ya existe un evento similar.')}"

            title = event.get("title") or args.get("title", "")
            date_str, time_str = self._parse_event_datetime(event)
            if not date_str:
                date_str = args.get("date", "")
            if not time_str:
                time_str = args.get("time", "09:00")
            location = event.get("location") or args.get("location", "")
            duration = args.get("duration_minutes", 60)

            response = f"📅 Evento creado:\n\"{title}\"\n📆 {self._format_date(date_str)} a las {time_str}"
            if location:
                response += f"\n📍 {location}"
            response += f"\n⏱️ Duración: {duration} min"

            sync_status = event.get("sync_status", "local")
            if sync_status != "synced":
                google_tip = await self._get_google_connect_tip(tenant_id, phone)
                if google_tip:
                    response += f"\n\n{google_tip}"

            return response

        elif tool_name == "listar_eventos":
            events = data.get("events", [])
            if not events:
                return "📅 No tenés eventos programados para ese período."

            response = "📅 Tus eventos:\n\n"
            for event in events[:10]:
                response += self._format_event_line(event) + "\n"

            return response.strip()

        elif tool_name == "modificar_evento":
            success = data.get("success", False)
            if success:
                event = data.get("event", {})
                title = event.get("title", "")
                date_str, time_str = self._parse_event_datetime(event)
                location = event.get("location", "")

                response = f"✏️ Evento modificado:\n\"{title}\""
                if date_str:
                    response += f"\n📆 {self._format_date(date_str)}"
                if time_str:
                    response += f" a las {time_str}"
                if location:
                    response += f"\n📍 {location}"
                return response
            else:
                candidates = data.get("candidates", [])
                if candidates:
                    response = f"⚠️ {data.get('message', 'Encontré varios eventos:')}\n\n"
                    for event in candidates[:5]:
                        response += self._format_event_line(event) + "\n"
                    return response.strip()
                return f"❌ {data.get('message', 'No encontré el evento para modificar.')}"

        elif tool_name == "eliminar_evento":
            success = data.get("success", False)
            if success:
                event = data.get("event", {})
                title = event.get("title", "")
                return f"✅ Evento cancelado:\n\"{title}\""
            else:
                candidates = data.get("candidates", [])
                if candidates:
                    response = f"⚠️ {data.get('message', 'Encontré varios eventos:')}\n\n"
                    for event in candidates[:5]:
                        response += self._format_event_line(event) + "\n"
                    return response.strip()
                return f"❌ {data.get('message', 'No encontré el evento para eliminar.')}"

        elif tool_name == "verificar_disponibilidad":
            available = data.get("available", False)
            if available:
                date_str = args.get("date", "")
                time_str = args.get("time", "")
                return f"✅ Tenés libre el {self._format_date(date_str)} a las {time_str}."
            else:
                conflicts = data.get("conflicts", [])
                suggested_times = data.get("suggested_times", [])
                response = "⚠️ A esa hora tenés:\n"
                for conflict in conflicts[:3]:
                    title = conflict.get("title", "")
                    _, c_time = self._parse_event_datetime(conflict)
                    response += f"• \"{title}\" a las {c_time}\n"
                if suggested_times:
                    response += "\nHorarios sugeridos:\n"
                    for s in suggested_times[:3]:
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
            if data:
                title = data.get("title", "")
                date_str, time_str = self._parse_event_datetime(data)
                location = data.get("location", "")

                response = f"📅 Tu próximo evento:\n\"{title}\""
                if date_str:
                    response += f"\n📆 {self._format_date(date_str)}"
                if time_str:
                    response += f" a las {time_str}"
                if location:
                    response += f"\n📍 {location}"
                return response
            else:
                return "📅 No tenés eventos próximos programados."

        elif tool_name == "crear_recordatorio":
            r_message = data.get("message", "")
            r_date = data.get("trigger_date", "")
            r_time = data.get("trigger_time", "")
            r_recurrence = data.get("recurrence", "none")

            response = f"⏰ Recordatorio creado:\n\"{r_message.capitalize()}\"\n📆 {self._format_date(r_date)} a las {r_time}"

            if r_recurrence != "none":
                recurrence_text = {
                    "daily": "Todos los días",
                    "weekly": "Todas las semanas",
                    "monthly": "Todos los meses",
                }
                response += f"\n🔄 {recurrence_text.get(r_recurrence, r_recurrence)}"

            return response

        elif tool_name == "listar_recordatorios":
            reminders = data.get("reminders", [])
            if not reminders:
                return "⏰ No tenés recordatorios pendientes.\n\n¿Querés que te recuerde algo?"

            response = "⏰ Tus recordatorios pendientes:\n\n"

            today = datetime.now().date()
            today_items: list[dict] = []
            tomorrow_items: list[dict] = []
            later_items: list[dict] = []
            recurring_items: list[dict] = []

            for r in reminders:
                if r.get("recurrence") != "none":
                    recurring_items.append(r)
                else:
                    r_date = datetime.strptime(r["trigger_date"], "%Y-%m-%d").date()
                    if r_date == today:
                        today_items.append(r)
                    elif r_date == today + timedelta(days=1):
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
                r_message = data.get("message", "")
                return f"✅ Recordatorio cancelado:\n\"{r_message}\""
            else:
                return "❌ No encontré un recordatorio que coincida."

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
