"""Centralized tool definitions for all domain agents."""

FINANCE_TOOLS = [
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
            "description": "Consulta reporte de gastos por período. Usar SIEMPRE para preguntas sobre cuántos gastos hay, cuánto se gastó, o resúmenes financieros.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year"],
                        "default": "month",
                        "description": "Período del reporte. Default: month. Solo usar 'day' si el usuario dice explícitamente 'hoy'.",
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

AGENDA_TOOLS = [
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
                    "recurrence": {
                        "type": "string",
                        "enum": ["none", "daily", "weekly", "monthly", "weekdays"],
                        "description": "Frecuencia de repetición",
                    },
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
                    "only_mine": {"type": "boolean", "description": "Solo eventos creados por mí (default: todos del hogar)"},
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
    {
        "type": "function",
        "function": {
            "name": "modificar_recordatorio",
            "description": "Modifica un recordatorio existente",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "Texto para buscar el recordatorio"},
                    "message": {"type": "string", "description": "Nuevo mensaje"},
                    "trigger_date": {"type": "string", "description": "Nueva fecha YYYY-MM-DD"},
                    "trigger_time": {"type": "string", "description": "Nueva hora HH:MM"},
                    "recurrence": {
                        "type": "string",
                        "enum": ["none", "daily", "weekly", "monthly"],
                        "description": "Nueva frecuencia",
                    },
                },
                "required": ["search_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "completar_recordatorio",
            "description": "Marca un recordatorio como completado/hecho",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "Texto para buscar el recordatorio"},
                },
                "required": ["search_query"],
            },
        },
    },
]

SHOPPING_TOOLS = [
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

VEHICLE_TOOLS = [
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

SUBSCRIPTION_ACQUISITION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_plans",
            "description": "Obtiene todos los planes disponibles con precios, límites y funcionalidades",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_checkout",
            "description": "Genera un link de pago de Lemon Squeezy para cualquier plan. El teléfono se obtiene automáticamente. NO pide home_name (se configura después del pago). REQUIERE email del usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "display_name": {
                        "type": "string",
                        "description": "Nombre del usuario",
                    },
                    "email": {
                        "type": "string",
                        "description": "Email del usuario (para facturación)",
                    },
                    "plan_type": {
                        "type": "string",
                        "enum": ["starter", "family", "premium"],
                        "description": "Tipo de plan",
                    },
                    "coupon_code": {
                        "type": "string",
                        "description": "Código de cupón (opcional)",
                    },
                },
                "required": ["display_name", "email", "plan_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_coupon",
            "description": "Valida un cupón de descuento",
            "parameters": {
                "type": "object",
                "properties": {
                    "coupon_code": {
                        "type": "string",
                        "description": "Código del cupón",
                    },
                    "plan_type": {
                        "type": "string",
                        "enum": ["starter", "family", "premium"],
                        "description": "Plan al que se aplicaría",
                    },
                },
                "required": ["coupon_code", "plan_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_payment_status",
            "description": "Verifica si el pago del usuario fue procesado. OBLIGATORIO usarla cuando el usuario dice que ya pagó. Consulta el estado real del registro.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

SUBSCRIPTION_SETUP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "complete_setup",
            "description": "Completa la configuración del hogar después del pago. Actualiza el nombre del hogar y marca el onboarding como completo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "home_name": {
                        "type": "string",
                        "description": "Nombre del hogar (ej: Casa García, Mi Depto)",
                    },
                },
                "required": ["home_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "invite_member",
            "description": "Invita a un miembro al hogar. Solo necesita el número de WhatsApp. El nombre se toma automáticamente cuando el invitado escriba.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Número de WhatsApp del invitado en formato +549...",
                    },
                },
                "required": ["phone"],
            },
        },
    },
]

SUBSCRIPTION_MANAGEMENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_plans",
            "description": "Obtiene todos los planes disponibles con precios, límites y funcionalidades",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_subscription_status",
            "description": "Consulta el plan actual, estado de suscripción y fecha de renovación",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_usage",
            "description": "Consulta mensajes usados/restantes este mes y cantidad de miembros",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_upgrade_checkout",
            "description": "Genera link de pago para upgrade de plan o reactivación",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_type": {
                        "type": "string",
                        "enum": ["family", "premium"],
                        "description": "Plan destino",
                    },
                },
                "required": ["plan_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_subscription",
            "description": "Cancela la suscripción. Solo usar después de confirmación explícita del usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Motivo de cancelación",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Debe ser true para ejecutar",
                    },
                },
                "required": ["reason", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "invite_member",
            "description": "Invita a un miembro al hogar. Solo necesita el número de WhatsApp. El nombre se toma automáticamente cuando el invitado escriba.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Número de WhatsApp del invitado en formato +549...",
                    },
                },
                "required": ["phone"],
            },
        },
    },
]
