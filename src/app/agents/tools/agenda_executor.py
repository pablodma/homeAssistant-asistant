"""Agenda domain tool executor — pure execution, no LLM logic."""

from datetime import datetime, timedelta
from typing import Any

import structlog

from ...services.backend_client import get_backend_client

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

async def execute_agenda_tool(
    tool_name: str,
    args: dict[str, Any],
    tenant_id: str,
    phone: str,
) -> dict[str, Any]:
    """Execute an agenda tool by calling the backend API.

    Extracted from CalendarAgent._execute_tool.
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
                "recurrence": args.get("recurrence"),
                "user_phone": phone,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            response = await backend.post(
                f"{base_path}/agent/calendar/event", json=payload,
            )
        elif tool_name == "listar_eventos":
            params = {k: v for k, v in args.items() if v and k != "only_mine"}
            params["user_phone"] = phone
            if args.get("only_mine"):
                params["only_mine"] = "true"
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
        elif tool_name == "modificar_recordatorio":
            params = {
                "search_query": args.get("search_query", ""),
                "user_phone": phone,
            }
            if args.get("message"):
                params["message"] = args["message"]
            if args.get("trigger_date"):
                params["trigger_date"] = args["trigger_date"]
            if args.get("trigger_time"):
                params["trigger_time"] = args["trigger_time"]
            if args.get("recurrence"):
                params["recurrence"] = args["recurrence"]
            response = await backend.put(
                f"{base_path}/agent/reminders/search", params=params,
            )
        elif tool_name == "completar_recordatorio":
            params = {
                "search_query": args.get("search_query", ""),
                "user_phone": phone,
            }
            response = await backend.post(
                f"{base_path}/agent/reminders/complete", params=params,
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


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _parse_event_datetime(event: dict[str, Any]) -> tuple[str, str]:
    """Extract date and time from an event's start_datetime field.

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


def _format_event_line(event: dict[str, Any]) -> str:
    """Format a single event as a display line."""
    title = event.get("title", "")
    _, time_str = _parse_event_datetime(event)
    location = event.get("location", "")
    line = f"• {time_str} - {title}" if time_str else f"• {title}"
    if event.get("creator_name"):
        line += f" (por {event['creator_name']})"
    if location:
        line += f"\n  📍 {location}"
    if event.get("is_recurring"):
        line = "🔄 " + line.lstrip("• ")
        line = "• " + line
    return line


def _format_date(date_str: str) -> str:
    """Format a date string for display (relative when close, full otherwise)."""
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


async def _get_google_connect_tip(tenant_id: str, phone: str) -> str | None:
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


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------

async def format_agenda_response(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
    tenant_id: str = "",
    phone: str = "",
) -> str:
    """Format agenda tool result for WhatsApp display.

    Extracted from CalendarAgent._generate_response.
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
        date_str, time_str = _parse_event_datetime(event)
        if not date_str:
            date_str = args.get("date", "")
        if not time_str:
            time_str = args.get("time", "09:00")
        location = event.get("location") or args.get("location", "")
        duration = args.get("duration_minutes", 60)

        response = f"📅 Evento creado:\n\"{title}\"\n📆 {_format_date(date_str)} a las {time_str}"
        if location:
            response += f"\n📍 {location}"
        response += f"\n⏱️ Duración: {duration} min"

        recurrence = event.get("recurrence_rule") or args.get("recurrence")
        if recurrence and recurrence != "none":
            recurrence_labels = {
                "daily": "Todos los días",
                "weekly": "Todas las semanas",
                "monthly": "Todos los meses",
                "weekdays": "De lunes a viernes",
            }
            response += f"\n🔄 {recurrence_labels.get(recurrence, recurrence)}"

        conflicts = data.get("conflicts")
        if conflicts:
            conflict_titles = [c.get("title", "") for c in conflicts[:2]]
            response += f"\n\n⚠️ Ojo: a esa hora también tenés: {', '.join(conflict_titles)}"

        sync_status = event.get("sync_status", "local")
        if sync_status != "synced":
            google_tip = await _get_google_connect_tip(tenant_id, phone)
            if google_tip:
                response += f"\n\n{google_tip}"

        return response

    elif tool_name == "listar_eventos":
        events = data.get("events", [])
        if not events:
            return "📅 No tenés eventos programados para ese período."

        response = "📅 Eventos:\n\n"
        for event in events[:10]:
            response += _format_event_line(event) + "\n"

        return response.strip()

    elif tool_name == "modificar_evento":
        success = data.get("success", False)
        if success:
            event = data.get("event", {})
            title = event.get("title", "")
            date_str, time_str = _parse_event_datetime(event)
            location = event.get("location", "")

            response = f"✏️ Evento modificado:\n\"{title}\""
            if date_str:
                response += f"\n📆 {_format_date(date_str)}"
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
                    response += _format_event_line(event) + "\n"
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
                    response += _format_event_line(event) + "\n"
                return response.strip()
            return f"❌ {data.get('message', 'No encontré el evento para eliminar.')}"

    elif tool_name == "verificar_disponibilidad":
        available = data.get("available", False)
        if available:
            date_str = args.get("date", "")
            time_str = args.get("time", "")
            return f"✅ Tenés libre el {_format_date(date_str)} a las {time_str}."
        else:
            conflicts = data.get("conflicts", [])
            suggested_times = data.get("suggested_times", [])
            response = "⚠️ A esa hora tenés:\n"
            for conflict in conflicts[:3]:
                title = conflict.get("title", "")
                _, c_time = _parse_event_datetime(conflict)
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
            date_str, time_str = _parse_event_datetime(data)
            location = data.get("location", "")

            response = f"📅 Tu próximo evento:\n\"{title}\""
            if date_str:
                response += f"\n📆 {_format_date(date_str)}"
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

        response = f"⏰ Recordatorio creado:\n\"{r_message.capitalize()}\"\n📆 {_format_date(r_date)} a las {r_time}"

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
                response += f"• {_format_date(r['trigger_date'])} {r['trigger_time']} - {r['message']}\n"
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

    elif tool_name == "modificar_recordatorio":
        updated = data.get("updated", False)
        if updated:
            r_message = data.get("message", "")
            return f"✏️ Recordatorio modificado:\n\"{r_message}\""
        else:
            return "❌ No encontré un recordatorio que coincida."

    elif tool_name == "completar_recordatorio":
        completed = data.get("completed", False)
        if completed:
            r_message = data.get("message", "")
            return f"✅ Recordatorio completado:\n\"{r_message}\""
        else:
            return "❌ No encontré un recordatorio que coincida."

    return "✓ Operación completada."
