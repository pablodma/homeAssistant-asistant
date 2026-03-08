"""Shopping domain tool executor — direct DB queries."""

from typing import Any

import structlog

from ...config.database import get_pool

logger = structlog.get_logger()


async def execute_shopping_tool(
    tool_name: str,
    args: dict[str, Any],
    tenant_id: str,
    phone: str,
) -> dict[str, Any]:
    """Execute a shopping tool via direct DB query.

    Extracted from ShoppingAgent._execute_tool.
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


def format_shopping_response(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> str:
    """Format shopping tool result for WhatsApp display.

    Extracted from ShoppingAgent._generate_response.
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
