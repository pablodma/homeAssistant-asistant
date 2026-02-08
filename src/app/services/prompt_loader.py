"""Prompt loading service."""

from typing import Optional

import structlog

from ..config.database import get_pool

logger = structlog.get_logger()

# Default prompts (fallback if not in database)
DEFAULT_PROMPTS = {
    "router": """Agente Orquestador Principal HomeAI. Te llamas Casita.

## Identidad

Eres el asistente virtual del hogar HomeAI. Ayudás a los usuarios a gestionar su hogar de forma simple y conversacional.

## Tus Capacidades

Tenés acceso a estas herramientas especializadas:

1. **finance_agent** - Para todo lo relacionado con dinero: registrar gastos, consultar cuánto se gastó, ver presupuestos.

2. **calendar_agent** - Para gestionar eventos y agenda: crear citas, ver qué hay programado, cancelar eventos.

3. **reminder_agent** - Para recordatorios y alertas: crear recordatorios, ver pendientes, cancelar recordatorios.

4. **shopping_agent** - Para listas de compras: agregar items, ver listas, marcar como comprado.

5. **vehicle_agent** - Para gestión del vehículo: registrar services, ver vencimientos (VTV, seguro), consultas de mantenimiento.

## Cómo Actuar

1. **Analizá el mensaje del usuario**
2. **Si está claro qué quiere** → Usá la herramienta correspondiente
3. **Si NO está claro** → Respondé directamente pidiendo clarificación (SIN usar herramientas)

## Tono y Estilo

- Español argentino informal (vos, gastaste, tenés)
- Respuestas concisas y directas
- Amigable pero no excesivamente efusivo
- Si algo no está claro, preguntá antes de asumir
""",
}


class PromptLoader:
    """Service for loading agent prompts."""

    async def get_prompt(
        self,
        agent_name: str,
        tenant_id: str,
    ) -> str:
        """Get the prompt for an agent.

        First tries to load from database, falls back to default.

        Args:
            agent_name: Name of the agent (router, finance, calendar, etc.)
            tenant_id: The tenant ID.

        Returns:
            The prompt content.
        """
        try:
            pool = await get_pool()

            query = """
                SELECT prompt_content
                FROM agent_prompts
                WHERE tenant_id = $1
                  AND agent_name = $2
                  AND is_active = true
                ORDER BY version DESC
                LIMIT 1
            """

            row = await pool.fetchrow(query, tenant_id, agent_name)

            if row:
                logger.debug(
                    "Loaded prompt from database",
                    agent_name=agent_name,
                    tenant_id=tenant_id,
                )
                return row["prompt_content"]

        except Exception as e:
            logger.warning(
                "Failed to load prompt from database, using default",
                agent_name=agent_name,
                error=str(e),
            )

        # Fallback to default
        return DEFAULT_PROMPTS.get(agent_name, f"You are the {agent_name} agent.")

    async def get_all_prompts(self, tenant_id: str) -> dict[str, str]:
        """Get all prompts for a tenant.

        Args:
            tenant_id: The tenant ID.

        Returns:
            Dictionary of agent_name -> prompt_content.
        """
        prompts = {}

        try:
            pool = await get_pool()

            query = """
                SELECT DISTINCT ON (agent_name) 
                    agent_name, prompt_content
                FROM agent_prompts
                WHERE tenant_id = $1 AND is_active = true
                ORDER BY agent_name, version DESC
            """

            rows = await pool.fetch(query, tenant_id)
            for row in rows:
                prompts[row["agent_name"]] = row["prompt_content"]

        except Exception as e:
            logger.warning(
                "Failed to load prompts from database",
                error=str(e),
            )

        # Merge with defaults
        for agent_name, default_prompt in DEFAULT_PROMPTS.items():
            if agent_name not in prompts:
                prompts[agent_name] = default_prompt

        return prompts
