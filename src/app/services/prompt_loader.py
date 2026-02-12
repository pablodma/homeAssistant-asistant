"""Prompt loading service.

Single source of truth: Configuration files (docs/prompts/*.md)

Simple architecture:
- Prompts live in docs/prompts/ as markdown files
- Versionado en git con code review
- Cambios se deployan automáticamente via Railway
- No hay DB para prompts (simplicidad)

Para cambiar un prompt:
1. Editar docs/prompts/{agent}-agent.md
2. Commit + push
3. Railway redeploya (~30 segundos)
"""

from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger()

# Path to prompt configuration files
# homeai-assis/docs/prompts/ - deployed with the app
PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "prompts"

# Mapping of agent names to their prompt files
PROMPT_FILES = {
    "finance": "finance-agent.md",
    "calendar": "calendar-agent.md",
    "reminder": "reminder-agent.md",
    "shopping": "shopping-agent.md",
    "vehicle": "vehicle-agent.md",
    "router": "router-agent.md",
    "qa": "qa-agent.md",
    "qa-reviewer": "qa-reviewer-agent.md",
}


def _load_prompt_from_file(agent_name: str) -> Optional[str]:
    """Load prompt from configuration file.
    
    Args:
        agent_name: Name of the agent (finance, calendar, etc.)
        
    Returns:
        Prompt content if file exists, None otherwise.
    """
    filename = PROMPT_FILES.get(agent_name)
    if not filename:
        logger.warning(
            "No prompt file configured for agent",
            agent_name=agent_name,
        )
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
        else:
            logger.warning(
                "Prompt file not found",
                agent_name=agent_name,
                path=str(prompt_path),
            )
    except Exception as e:
        logger.error(
            "Failed to load prompt from file",
            agent_name=agent_name,
            path=str(prompt_path),
            error=str(e),
        )
    
    return None


class PromptLoader:
    """Service for loading agent prompts from configuration files.
    
    Simple, file-based prompt management:
    - One markdown file per agent
    - Versionado en git
    - No database complexity
    """

    async def get_prompt(
        self,
        agent_name: str,
        tenant_id: str,  # Kept for interface compatibility, not used
    ) -> str:
        """Get the prompt for an agent.

        Loads from docs/prompts/{agent}-agent.md

        Args:
            agent_name: Name of the agent (router, finance, calendar, etc.)
            tenant_id: The tenant ID (unused - single-tenant for now).

        Returns:
            The prompt content.
        """
        prompt = _load_prompt_from_file(agent_name)
        
        if prompt:
            return prompt
        
        # Last resort - generic prompt (should not happen if files exist)
        logger.error(
            "No prompt found for agent - using generic fallback",
            agent_name=agent_name,
        )
        return f"Sos el agente de {agent_name} de HomeAI. Ayudá al usuario con sus consultas."

    async def get_all_prompts(self, tenant_id: str) -> dict[str, str]:
        """Get all prompts.

        Args:
            tenant_id: The tenant ID (unused).

        Returns:
            Dictionary of agent_name -> prompt_content.
        """
        prompts = {}
        
        for agent_name in PROMPT_FILES.keys():
            prompt = _load_prompt_from_file(agent_name)
            if prompt:
                prompts[agent_name] = prompt
        
        return prompts
