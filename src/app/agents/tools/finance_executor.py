"""Finance domain tool executor — pure execution, no LLM logic."""

import json
from typing import Any, Optional

import structlog

from ...services.backend_client import get_backend_client
from ...services.quality_logger import get_quality_logger

logger = structlog.get_logger()


async def execute_finance_tool(
    tool_name: str,
    args: dict[str, Any],
    tenant_id: str,
    phone: str = "",
    message_in: str = "",
) -> dict[str, Any]:
    """Execute a finance tool by calling the backend API.

    This function ONLY executes the API call - no business logic decisions.
    For ``completar_configuracion_inicial`` it returns a marker so the calling
    agent can handle the actual first-time completion flow.

    Args:
        tool_name: Name of the tool.
        args: Tool arguments.
        tenant_id: The tenant ID.
        phone: User's phone for error logging.
        message_in: Original message for error logging.

    Returns:
        Tool execution result dict with ``success`` and ``data``/``error``.
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
            # Return marker — the calling agent handles the actual first-time
            # completion (it needs access to complete_first_time on the agent).
            return {"success": True, "data": {"first_time_tool": True}}
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
                agent_name="finance",
                tool_name=tool_name,
                user_phone=phone,
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
            agent_name="finance",
            tool_name=tool_name,
            user_phone=phone,
            message_in=message_in,
            severity="high",
            exception=e,
        )
        return {"success": False, "error": str(e)}


def format_finance_tool_result_for_llm(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> str:
    """Format tool result for LLM reasoning (not for user display)."""
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
            return "No hay ingresos registrados en este periodo."
        total = float(data.get("total", 0))
        lines = [f"Ingresos del periodo (total: ${total:,.0f}):"]
        for inc in incomes:
            desc = inc.get("description") or "Sin descripcion"
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
            cat = e.get("category_name", "Sin categoria")
            desc = e.get("description") or ""
            dt = e.get("expense_date", "")
            lines.append(f"- ${amt:,.0f} en {cat} - {desc} ({dt}) [id: {e.get('id', '')}]")
        return "\n".join(lines)

    return json.dumps(data, ensure_ascii=False)


def format_finance_response(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> str:
    """Format tool result for WhatsApp display.

    This function ONLY formats data - no business logic decisions.

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
