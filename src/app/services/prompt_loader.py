"""Prompt loading service.

Single source of truth: Database (agent_prompts table)

Flow:
1. Try to load from database
2. If not found, load from file (docs/prompts/) and SEED to database
3. Last resort: hardcoded defaults (router/qa only)

This ensures:
- DB is always the runtime source
- Files are the versioned source that gets auto-seeded
- Admin panel shows what the bot actually uses
"""

from pathlib import Path
from typing import Optional
from uuid import UUID

import structlog

from ..config.database import get_pool

logger = structlog.get_logger()

# Path to documented prompts (inside this repo)
# homeai-assis/docs/prompts/ - available in production on Railway
PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "prompts"

# Mapping of agent names to their prompt files
PROMPT_FILES = {
    "finance": "finance-agent.md",
    "calendar": "calendar-agent.md",
    "reminder": "reminder-agent.md",
    "shopping": "shopping-agent.md",
    "vehicle": "vehicle-agent.md",
}

# Default prompts (fallback ONLY for router/qa which don't have markdown files)
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
    "qa": """Sos un agente de control de calidad para un bot de WhatsApp llamado HomeAI.
Tu trabajo es analizar interacciones y detectar problemas de calidad.

## Tipos de problemas a detectar

1. **misinterpretation**: El bot malinterpretó lo que el usuario quería hacer
   - Ejemplo: Usuario pide "agregar leche" y el bot registra un gasto en vez de agregarlo a la lista

2. **hallucination**: El bot confirmó algo que no hizo o inventó información
   - Ejemplo: Bot dice "Registré el gasto" pero tool_result muestra error
   - Ejemplo: Bot menciona datos que no están en el resultado

3. **unsupported_case**: El usuario pidió algo que el bot no puede hacer
   - Ejemplo: Usuario pide exportar datos a Excel y el bot no tiene esa función
   - Nota: Solo es problema si el bot NO aclara que no puede hacerlo

4. **incomplete_response**: La respuesta está incompleta o falta información importante
   - Ejemplo: Usuario pregunta "cuánto gasté este mes" y bot responde sin dar el total

## Análisis

Evaluá si la respuesta del bot es correcta, útil y honesta.
Considerá especialmente si el bot confirmó acciones que fallaron (hallucination).

## Formato de respuesta

- has_issue: true si detectaste un problema, false si la interacción es correcta
- category: uno de los 4 tipos si has_issue=true, null si has_issue=false
- explanation: explicación breve del problema detectado (en español)
- suggestion: sugerencia de mejora para el prompt o código (en español)
- confidence: qué tan seguro estás del análisis (0.0 a 1.0)
""",
}


def _load_prompt_from_file(agent_name: str) -> Optional[str]:
    """Load prompt from markdown file in docs/prompts/.
    
    Args:
        agent_name: Name of the agent (finance, calendar, etc.)
        
    Returns:
        Prompt content if file exists, None otherwise.
    """
    filename = PROMPT_FILES.get(agent_name)
    if not filename:
        return None
    
    prompt_path = PROMPTS_DIR / filename
    
    try:
        if prompt_path.exists():
            content = prompt_path.read_text(encoding="utf-8")
            logger.debug(
                "Loaded prompt from file",
                agent_name=agent_name,
                path=str(prompt_path),
            )
            return content
    except Exception as e:
        logger.warning(
            "Failed to load prompt from file",
            agent_name=agent_name,
            path=str(prompt_path),
            error=str(e),
        )
    
    return None


class PromptLoader:
    """Service for loading agent prompts.
    
    Single source of truth: Database.
    Auto-seeds from files if not in DB.
    """

    async def _seed_prompt_to_db(
        self,
        tenant_id: str,
        agent_name: str,
        prompt_content: str,
    ) -> bool:
        """Seed a prompt to database if it doesn't exist.
        
        Returns True if seeded successfully.
        """
        try:
            pool = await get_pool()
            
            # Check if already exists
            check_query = """
                SELECT 1 FROM agent_prompts 
                WHERE tenant_id = $1 AND agent_name = $2 AND is_active = true
            """
            exists = await pool.fetchrow(check_query, tenant_id, agent_name)
            
            if exists:
                return False  # Already exists, don't overwrite
            
            # Insert the prompt
            insert_query = """
                INSERT INTO agent_prompts (tenant_id, agent_name, prompt_content, version, is_active, created_by)
                VALUES ($1, $2, $3, 1, true, 'system-seed')
            """
            await pool.execute(insert_query, tenant_id, agent_name, prompt_content)
            
            logger.info(
                "Seeded prompt to database",
                agent_name=agent_name,
                tenant_id=tenant_id,
            )
            return True
            
        except Exception as e:
            logger.warning(
                "Failed to seed prompt to database",
                agent_name=agent_name,
                error=str(e),
            )
            return False

    async def get_prompt(
        self,
        agent_name: str,
        tenant_id: str,
    ) -> str:
        """Get the prompt for an agent.

        Flow:
        1. Try database (single source of truth for runtime)
        2. If not found, load from file and SEED to database
        3. Fallback to hardcoded (router/qa only)

        Args:
            agent_name: Name of the agent (router, finance, calendar, etc.)
            tenant_id: The tenant ID.

        Returns:
            The prompt content.
        """
        # 1. Try database first (single source of truth)
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
                "Failed to load prompt from database",
                agent_name=agent_name,
                error=str(e),
            )

        # 2. Not in DB - try to load from file and seed to DB
        file_prompt = _load_prompt_from_file(agent_name)
        if file_prompt:
            # Seed to database so admin panel can see/edit it
            await self._seed_prompt_to_db(tenant_id, agent_name, file_prompt)
            return file_prompt

        # 3. Fallback to hardcoded default (only router/qa have these)
        if agent_name in DEFAULT_PROMPTS:
            default_prompt = DEFAULT_PROMPTS[agent_name]
            # Seed to database
            await self._seed_prompt_to_db(tenant_id, agent_name, default_prompt)
            return default_prompt

        # 4. Last resort - generic prompt (should not happen)
        logger.warning(
            "No prompt found for agent - using generic fallback",
            agent_name=agent_name,
            tenant_id=tenant_id,
        )
        return f"You are the {agent_name} agent."

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
