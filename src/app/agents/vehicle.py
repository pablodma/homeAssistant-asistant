"""Vehicle Agent - Vehicle and maintenance management."""

import json
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog
from openai import AsyncOpenAI

from ..config import get_settings
from ..config.database import get_pool
from .base import FIRST_TIME_TOOL_DEFINITION, AgentResult, BaseAgent

logger = structlog.get_logger()


class VehicleAgent(BaseAgent):
    """Agent for managing vehicles and maintenance."""

    name = "vehicle"

    def __init__(self):
        """Initialize the vehicle agent."""
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
        """Process a vehicle-related message.

        Args:
            message: The user's message.
            phone: The user's phone number.
            tenant_id: The tenant ID.
            history: Conversation history.

        Returns:
            The agent's response.
        """
        logger.info("Vehicle agent processing", message=message[:50])

        prompt = await self.get_prompt(tenant_id)

        # Build messages
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": f"Hoy es {['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'][datetime.now().weekday()]} {datetime.now().strftime('%Y-%m-%d')}."},
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
                    "name": "registrar_vehiculo",
                    "description": "Registra un nuevo vehículo",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "brand": {"type": "string", "description": "Marca"},
                            "model": {"type": "string", "description": "Modelo"},
                            "year": {"type": "integer", "description": "Año"},
                            "plate": {"type": "string", "description": "Patente"},
                            "mileage": {"type": "integer", "description": "Kilometraje actual"},
                            "vehicle_name": {"type": "string", "description": "Apodo del vehículo"},
                        },
                        "required": ["brand", "model", "year"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ver_vehiculo",
                    "description": "Ver datos y estado del vehículo",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "vehicle_name": {"type": "string", "description": "Nombre del vehículo"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "registrar_service",
                    "description": "Registra un service o mantenimiento",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service_type": {"type": "string", "description": "Tipo de service"},
                            "service_date": {"type": "string", "description": "Fecha YYYY-MM-DD"},
                            "mileage": {"type": "integer", "description": "Kilometraje"},
                            "cost": {"type": "number", "description": "Costo"},
                            "notes": {"type": "string", "description": "Notas"},
                        },
                        "required": ["service_type"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ver_historial_services",
                    "description": "Ver historial de services",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "vehicle_name": {"type": "string", "description": "Nombre del vehículo"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "configurar_vencimiento",
                    "description": "Configura fecha de vencimiento (VTV, seguro, patente)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reminder_type": {
                                "type": "string",
                                "enum": ["vtv", "seguro", "patente", "service"],
                                "description": "Tipo de vencimiento",
                            },
                            "due_date": {"type": "string", "description": "Fecha de vencimiento YYYY-MM-DD"},
                        },
                        "required": ["reminder_type", "due_date"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ver_vencimientos",
                    "description": "Ver próximos vencimientos",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "actualizar_kilometraje",
                    "description": "Actualiza el kilometraje actual",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mileage": {"type": "integer", "description": "Nuevo kilometraje"},
                        },
                        "required": ["mileage"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "consultar_tips",
                    "description": "Consulta consejos de mantenimiento",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Consulta sobre mantenimiento"},
                        },
                        "required": ["query"],
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

                logger.info(f"Vehicle tool call: {tool_name}", args=tool_args)

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

            # Direct response (for tips/questions)
            return AgentResult(
                response=choice.message.content or "No pude procesar tu solicitud.",
                agent_used=self.name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

        except Exception as e:
            logger.error("Vehicle agent error", error=str(e))
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
        """Execute a vehicle tool.

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

            if tool_name == "registrar_vehiculo":
                brand = args.get("brand", "")
                model = args.get("model", "")
                year = args.get("year", 0)
                plate = args.get("plate", "")
                mileage = args.get("mileage", 0)
                vehicle_name = args.get("vehicle_name", f"{brand} {model}")

                query = """
                    INSERT INTO vehicles (
                        tenant_id, user_phone, brand, model, year, plate, mileage, vehicle_name
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (tenant_id, plate) DO UPDATE SET
                        mileage = EXCLUDED.mileage, updated_at = NOW()
                    RETURNING id
                """
                await pool.execute(
                    query, tenant_id, phone, brand, model, year, plate, mileage, vehicle_name
                )
                return {
                    "success": True,
                    "data": {
                        "brand": brand,
                        "model": model,
                        "year": year,
                        "plate": plate,
                        "mileage": mileage,
                        "vehicle_name": vehicle_name,
                    },
                }

            elif tool_name == "ver_vehiculo":
                query = """
                    SELECT v.*, 
                           (SELECT MAX(service_date) FROM vehicle_services vs WHERE vs.vehicle_id = v.id) as last_service
                    FROM vehicles v
                    WHERE v.tenant_id = $1 AND v.user_phone = $2
                    LIMIT 1
                """
                row = await pool.fetchrow(query, tenant_id, phone)
                if row:
                    # Get reminders
                    reminders_query = """
                        SELECT reminder_type, due_date FROM vehicle_reminders
                        WHERE vehicle_id = $1
                        ORDER BY due_date
                    """
                    reminders = await pool.fetch(reminders_query, row["id"])

                    return {
                        "success": True,
                        "data": {
                            "brand": row["brand"],
                            "model": row["model"],
                            "year": row["year"],
                            "plate": row["plate"],
                            "mileage": row["mileage"],
                            "vehicle_name": row["vehicle_name"],
                            "last_service": row["last_service"].strftime("%Y-%m-%d") if row["last_service"] else None,
                            "reminders": [
                                {"type": r["reminder_type"], "due_date": r["due_date"].strftime("%Y-%m-%d")}
                                for r in reminders
                            ],
                        },
                    }
                else:
                    return {"success": True, "data": None}

            elif tool_name == "registrar_service":
                # Get vehicle
                vehicle_query = "SELECT id, mileage FROM vehicles WHERE tenant_id = $1 AND user_phone = $2 LIMIT 1"
                vehicle = await pool.fetchrow(vehicle_query, tenant_id, phone)

                if not vehicle:
                    return {"success": False, "error": "No tenés un vehículo registrado"}

                service_type = args.get("service_type", "")
                service_date = args.get("service_date", datetime.now().strftime("%Y-%m-%d"))
                mileage = args.get("mileage", vehicle["mileage"])
                cost = args.get("cost", 0)
                notes = args.get("notes", "")

                query = """
                    INSERT INTO vehicle_services (
                        vehicle_id, service_type, service_date, mileage, cost, notes
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                """
                await pool.execute(query, vehicle["id"], service_type, service_date, mileage, cost, notes)

                # Update vehicle mileage if higher
                if mileage > vehicle["mileage"]:
                    await pool.execute(
                        "UPDATE vehicles SET mileage = $1 WHERE id = $2",
                        mileage, vehicle["id"]
                    )

                return {
                    "success": True,
                    "data": {
                        "service_type": service_type,
                        "service_date": service_date,
                        "mileage": mileage,
                        "cost": cost,
                    },
                }

            elif tool_name == "ver_historial_services":
                vehicle_query = "SELECT id, vehicle_name FROM vehicles WHERE tenant_id = $1 AND user_phone = $2 LIMIT 1"
                vehicle = await pool.fetchrow(vehicle_query, tenant_id, phone)

                if not vehicle:
                    return {"success": True, "data": {"services": [], "vehicle_name": None}}

                services_query = """
                    SELECT service_type, service_date, mileage, cost, notes
                    FROM vehicle_services
                    WHERE vehicle_id = $1
                    ORDER BY service_date DESC
                    LIMIT 10
                """
                rows = await pool.fetch(services_query, vehicle["id"])

                services = [
                    {
                        "service_type": r["service_type"],
                        "service_date": r["service_date"].strftime("%Y-%m-%d"),
                        "mileage": r["mileage"],
                        "cost": r["cost"],
                    }
                    for r in rows
                ]

                total_cost = sum(s["cost"] for s in services if s["cost"])

                return {
                    "success": True,
                    "data": {
                        "services": services,
                        "vehicle_name": vehicle["vehicle_name"],
                        "total_cost": total_cost,
                    },
                }

            elif tool_name == "configurar_vencimiento":
                vehicle_query = "SELECT id FROM vehicles WHERE tenant_id = $1 AND user_phone = $2 LIMIT 1"
                vehicle = await pool.fetchrow(vehicle_query, tenant_id, phone)

                if not vehicle:
                    return {"success": False, "error": "No tenés un vehículo registrado"}

                reminder_type = args.get("reminder_type", "")
                due_date = args.get("due_date", "")

                query = """
                    INSERT INTO vehicle_reminders (vehicle_id, reminder_type, due_date)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (vehicle_id, reminder_type) DO UPDATE SET
                        due_date = EXCLUDED.due_date, updated_at = NOW()
                """
                await pool.execute(query, vehicle["id"], reminder_type, due_date)

                return {
                    "success": True,
                    "data": {"reminder_type": reminder_type, "due_date": due_date},
                }

            elif tool_name == "ver_vencimientos":
                vehicle_query = "SELECT id, vehicle_name FROM vehicles WHERE tenant_id = $1 AND user_phone = $2 LIMIT 1"
                vehicle = await pool.fetchrow(vehicle_query, tenant_id, phone)

                if not vehicle:
                    return {"success": True, "data": {"reminders": []}}

                reminders_query = """
                    SELECT reminder_type, due_date
                    FROM vehicle_reminders
                    WHERE vehicle_id = $1
                    ORDER BY due_date
                """
                rows = await pool.fetch(reminders_query, vehicle["id"])

                reminders = [
                    {
                        "type": r["reminder_type"],
                        "due_date": r["due_date"].strftime("%Y-%m-%d"),
                        "days_until": (r["due_date"] - datetime.now().date()).days,
                    }
                    for r in rows
                ]

                return {
                    "success": True,
                    "data": {"reminders": reminders, "vehicle_name": vehicle["vehicle_name"]},
                }

            elif tool_name == "actualizar_kilometraje":
                mileage = args.get("mileage", 0)

                result = await pool.execute(
                    "UPDATE vehicles SET mileage = $1, updated_at = NOW() WHERE tenant_id = $2 AND user_phone = $3",
                    mileage, tenant_id, phone
                )

                return {"success": True, "data": {"mileage": mileage}}

            elif tool_name == "consultar_tips":
                # This would be handled by the LLM directly
                return {"success": True, "data": {"handled_by_llm": True}}

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
            logger.error("Vehicle tool failed", error=result.get("error"), tool=tool_name)
            return "Hubo un problema procesando tu solicitud. Intentá de nuevo."

        data = result.get("data", {})

        if tool_name == "registrar_vehiculo":
            brand = data.get("brand", "")
            model = data.get("model", "")
            year = data.get("year", "")
            plate = data.get("plate", "")
            mileage = data.get("mileage", 0)

            response = f"🚗 Vehículo registrado:\n{brand} {model} ({year})"
            if plate:
                response += f"\n• Patente: {plate}"
            if mileage:
                response += f"\n• Km actuales: {mileage:,}"
            response += "\n\n¿Querés que configure recordatorios para VTV, seguro y services?"
            return response

        elif tool_name == "ver_vehiculo":
            if not data:
                return "🚗 No tenés un vehículo registrado.\n\n¿Querés agregar uno?"

            brand = data.get("brand", "")
            model = data.get("model", "")
            year = data.get("year", "")
            plate = data.get("plate", "")
            mileage = data.get("mileage", 0)
            last_service = data.get("last_service", "")
            reminders = data.get("reminders", [])

            response = f"🚗 {data.get('vehicle_name', brand + ' ' + model)}\n"
            response += f"{brand} {model} ({year})\n"
            if plate:
                response += f"• Patente: {plate}\n"
            response += f"• Km actuales: {mileage:,}\n"
            if last_service:
                response += f"• Último service: {last_service}\n"

            if reminders:
                response += "\n📋 Próximos vencimientos:\n"
                for r in reminders:
                    days = (datetime.strptime(r["due_date"], "%Y-%m-%d").date() - datetime.now().date()).days
                    status = "⚠️" if days < 30 else "✓"
                    response += f"• {r['type'].upper()}: {r['due_date']} ({days} días) {status}\n"

            return response.strip()

        elif tool_name == "registrar_service":
            service_type = data.get("service_type", "")
            service_date = data.get("service_date", "")
            mileage = data.get("mileage", 0)
            cost = data.get("cost", 0)

            response = f"✅ Service registrado:\n🔧 {service_type}\n📆 {service_date}\n📍 {mileage:,} km"
            if cost:
                response += f"\n💰 ${cost:,.0f}"
            return response

        elif tool_name == "ver_historial_services":
            services = data.get("services", [])
            vehicle_name = data.get("vehicle_name", "")
            total_cost = data.get("total_cost", 0)

            if not services:
                return "🔧 No hay services registrados."

            response = f"🔧 Historial - {vehicle_name}:\n\n"
            for s in services[:5]:
                response += f"📅 {s['service_date']} ({s['mileage']:,} km):\n"
                response += f"• {s['service_type']}"
                if s.get("cost"):
                    response += f" - ${s['cost']:,.0f}"
                response += "\n\n"

            response += f"📊 Total gastado: ${total_cost:,.0f}"
            return response.strip()

        elif tool_name == "configurar_vencimiento":
            reminder_type = data.get("reminder_type", "").upper()
            due_date = data.get("due_date", "")
            return f"✅ Recordatorio configurado:\n📌 {reminder_type}: vence el {due_date}"

        elif tool_name == "ver_vencimientos":
            reminders = data.get("reminders", [])
            vehicle_name = data.get("vehicle_name", "")

            if not reminders:
                return "📋 No tenés vencimientos configurados."

            response = f"📋 Recordatorios - {vehicle_name}:\n\n"

            overdue = [r for r in reminders if r["days_until"] < 0]
            upcoming = [r for r in reminders if 0 <= r["days_until"] <= 30]
            later = [r for r in reminders if r["days_until"] > 30]

            if overdue:
                response += "⚠️ Vencidos:\n"
                for r in overdue:
                    response += f"• {r['type'].upper()}: venció hace {abs(r['days_until'])} días\n"
                response += "\n"

            if upcoming:
                response += "📅 Próximos 30 días:\n"
                for r in upcoming:
                    response += f"• {r['type'].upper()}: {r['due_date']} (en {r['days_until']} días)\n"
                response += "\n"

            if later:
                response += "📆 Más adelante:\n"
                for r in later:
                    response += f"• {r['type'].upper()}: {r['due_date']}\n"

            return response.strip()

        elif tool_name == "actualizar_kilometraje":
            mileage = data.get("mileage", 0)
            return f"✅ Kilometraje actualizado: {mileage:,} km"

        return "✓ Operación completada."
