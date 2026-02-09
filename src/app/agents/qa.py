"""QA Agent - Quality Assurance for bot interactions."""

import json
from dataclasses import dataclass
from typing import Any, Optional

import structlog
from openai import AsyncOpenAI

from ..config import get_settings

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
    """

    ANALYSIS_PROMPT = """Sos un agente de control de calidad para un bot de WhatsApp llamado HomeAI.
Tu trabajo es analizar interacciones y detectar problemas de calidad.

## Interacción a analizar

**Usuario dijo:** {message_in}

**Bot respondió:** {message_out}

**Agente usado:** {agent_name}

**Herramienta ejecutada:** {tool_name}

**Resultado de la herramienta:** {tool_result}

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

Respondé SOLO con JSON válido (sin markdown):
{{"has_issue": boolean, "category": string|null, "explanation": string|null, "suggestion": string|null, "confidence": float}}

- has_issue: true si detectaste un problema, false si la interacción es correcta
- category: uno de los 4 tipos si has_issue=true, null si has_issue=false
- explanation: explicación breve del problema detectado (en español)
- suggestion: sugerencia de mejora para el prompt o código (en español)
- confidence: qué tan seguro estás del análisis (0.0 a 1.0)

Si no hay problema, respondé: {{"has_issue": false, "category": null, "explanation": null, "suggestion": null, "confidence": 1.0}}"""

    def __init__(self):
        """Initialize the QA agent."""
        self.settings = get_settings()
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    async def analyze(
        self,
        message_in: str,
        message_out: str,
        agent_name: str,
        tool_name: Optional[str] = None,
        tool_result: Optional[dict[str, Any]] = None,
    ) -> QAAnalysisResult:
        """Analyze an interaction for quality issues.

        Args:
            message_in: The user's original message.
            message_out: The bot's response.
            agent_name: Which agent processed the message.
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

            # Build prompt
            prompt = self.ANALYSIS_PROMPT.format(
                message_in=message_in,
                message_out=message_out,
                agent_name=agent_name or "router",
                tool_name=tool_name or "N/A",
                tool_result=tool_result_str,
            )

            # Call LLM
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",  # Cheaper model for QA
                messages=[
                    {"role": "system", "content": "Sos un analizador de calidad. Respondé SOLO con JSON válido."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=500,
                temperature=0.1,  # Low temperature for consistent analysis
            )

            # Parse response
            content = response.choices[0].message.content or "{}"
            
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
