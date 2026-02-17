"""QA Agent - Quality Assurance for bot interactions."""

import json
from dataclasses import dataclass
from typing import Any, Optional

import structlog
from anthropic import AsyncAnthropic

from ..config import get_settings
from ..services.prompt_loader import PromptLoader

logger = structlog.get_logger()


@dataclass
class QAAnalysisResult:
    """Result of QA analysis."""

    has_issue: bool
    category: Optional[str] = None  # misinterpretation, hallucination, unsupported_case, incomplete_response
    explanation: Optional[str] = None
    suggestion: Optional[str] = None
    confidence: float = 0.0


class QAAgent:
    """Agent for quality assurance analysis of bot interactions.

    Analyzes each interaction asynchronously to detect:
    - Misinterpretations: Bot misunderstood user intent
    - Hallucinations: Bot confirmed something that didn't happen
    - Unsupported cases: User requested something bot can't do
    - Incomplete responses: Response missing important information

    Uses Claude Opus via Anthropic SDK.
    """

    name = "qa"

    # Template for the interaction data (appended to the system prompt)
    INTERACTION_TEMPLATE = """

## Interacción a analizar

**Usuario dijo:** {message_in}

**Bot respondió:** {message_out}

**Agente usado:** {agent_name}

**Herramienta ejecutada:** {tool_name}

**Resultado de la herramienta:** {tool_result}

Respondé SOLO con JSON válido (sin markdown):
{{"has_issue": boolean, "category": string|null, "explanation": string|null, "suggestion": string|null, "confidence": float}}

Si no hay problema, respondé: {{"has_issue": false, "category": null, "explanation": null, "suggestion": null, "confidence": 1.0}}"""

    def __init__(self):
        """Initialize the QA agent."""
        self.settings = get_settings()
        self.client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        self.prompt_loader = PromptLoader()
        self._prompt_cache: dict[str, str] = {}

    async def get_prompt(self, tenant_id: str) -> str:
        """Get the prompt for this agent.

        Args:
            tenant_id: The tenant ID.

        Returns:
            The prompt content.
        """
        if tenant_id not in self._prompt_cache:
            self._prompt_cache[tenant_id] = await self.prompt_loader.get_prompt(self.name, tenant_id)
        return self._prompt_cache[tenant_id]

    async def analyze(
        self,
        message_in: str,
        message_out: str,
        agent_name: str,
        tenant_id: str | None,
        tool_name: Optional[str] = None,
        tool_result: Optional[dict[str, Any]] = None,
    ) -> QAAnalysisResult:
        """Analyze an interaction for quality issues.

        Args:
            message_in: The user's original message.
            message_out: The bot's response.
            agent_name: Which agent processed the message.
            tenant_id: The tenant ID (for custom prompts).
            tool_name: The tool that was executed (if any).
            tool_result: The result of the tool execution (if any).

        Returns:
            QAAnalysisResult with analysis findings.
        """
        try:
            # Format tool result for prompt
            tool_result_str = "N/A (no se ejecutó herramienta)"
            if tool_result is not None:
                # Sanitize sensitive data
                sanitized = self._sanitize_result(tool_result)
                tool_result_str = json.dumps(sanitized, ensure_ascii=False, indent=2)

            # Get system prompt (may be customized via admin)
            system_prompt = await self.get_prompt(tenant_id)

            # Build user prompt with interaction data
            user_prompt = self.INTERACTION_TEMPLATE.format(
                message_in=message_in,
                message_out=message_out,
                agent_name=agent_name or "router",
                tool_name=tool_name or "N/A",
                tool_result=tool_result_str,
            )

            # Call Claude via Anthropic SDK
            response = await self.client.messages.create(
                model=self.settings.qa_model,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=500,
                temperature=0.1,  # Low temperature for consistent analysis
            )

            # Parse response
            content = response.content[0].text if response.content else "{}"

            # Clean markdown if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            result = json.loads(content)

            return QAAnalysisResult(
                has_issue=result.get("has_issue", False),
                category=result.get("category"),
                explanation=result.get("explanation"),
                suggestion=result.get("suggestion"),
                confidence=float(result.get("confidence", 0.0)),
            )

        except json.JSONDecodeError as e:
            logger.warning("QA Agent failed to parse LLM response", error=str(e))
            return QAAnalysisResult(has_issue=False, confidence=0.0)

        except Exception as e:
            logger.error("QA Agent analysis failed", error=str(e))
            return QAAnalysisResult(has_issue=False, confidence=0.0)

    def _sanitize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive data from tool result before sending to LLM.

        Args:
            result: The raw tool result.

        Returns:
            Sanitized result safe for LLM analysis.
        """
        # Keys to remove (tokens, secrets, etc.)
        sensitive_keys = {"token", "secret", "password", "api_key", "access_token"}
        
        def sanitize_dict(d: dict) -> dict:
            sanitized = {}
            for key, value in d.items():
                key_lower = key.lower()
                if any(s in key_lower for s in sensitive_keys):
                    sanitized[key] = "[REDACTED]"
                elif isinstance(value, dict):
                    sanitized[key] = sanitize_dict(value)
                elif isinstance(value, list):
                    sanitized[key] = [
                        sanitize_dict(item) if isinstance(item, dict) else item
                        for item in value
                    ]
                else:
                    sanitized[key] = value
            return sanitized

        return sanitize_dict(result)

    def should_analyze(
        self,
        tool_result: Optional[dict[str, Any]] = None,
        sample_rate: float = 1.0,
    ) -> bool:
        """Determine if this interaction should be analyzed.

        Can be used to implement sampling to reduce costs.

        Args:
            tool_result: The tool result (if any).
            sample_rate: Fraction of interactions to analyze (0.0 to 1.0).

        Returns:
            True if should analyze, False to skip.
        """
        # Always analyze if tool execution failed
        if tool_result and not tool_result.get("success", True):
            return True

        # Sample other interactions based on rate
        if sample_rate < 1.0:
            import random
            return random.random() < sample_rate

        return True
