"""Output guard for LLM response validation.

Checks responses before sending to the user for:
- Prompt leakage (canary token detection)
- Excessive length
- Sensitive data patterns (tokens, secrets)
"""

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

MAX_RESPONSE_LENGTH = 3500
FALLBACK_RESPONSE = "Hubo un problema procesando tu mensaje. Por favor, intentá de nuevo."

CANARY_TOKENS: dict[str, str] = {
    "router": "CNRY-RTR-7k9xQ",
    "finance": "CNRY-FIN-m3pWz",
    "agenda": "CNRY-AGD-m4kTz",
    "shopping": "CNRY-SHP-y5tNf",
    "vehicle": "CNRY-VHC-d4wKb",
    "subscription": "CNRY-SUB-g6cXa",
}

_SENSITIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"sk-[a-zA-Z0-9\-_]{20,}", re.IGNORECASE),
    re.compile(r"sk-ant-api\d{2}-[a-zA-Z0-9\-_]{20,}", re.IGNORECASE),
    re.compile(r"eyJ[a-zA-Z0-9\-_]{30,}\.[a-zA-Z0-9\-_]{30,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{20,}"),
    re.compile(r"GOCSPX-[a-zA-Z0-9\-_]{20,}"),
]


@dataclass
class OutputGuardResult:
    """Result of output guard check."""
    text: str
    was_modified: bool
    leak_detected: bool
    sensitive_detected: bool
    was_truncated: bool


def check_response(response: str, agent_name: str | None = None) -> OutputGuardResult:
    """Validate an LLM response before sending to the user.

    Replaces the entire response if a canary token or sensitive data is found.
    Truncates if excessively long.
    """
    was_modified = False
    leak_detected = False
    sensitive_detected = False
    was_truncated = False

    for name, token in CANARY_TOKENS.items():
        if token in response:
            logger.critical(
                "prompt_leak_detected",
                agent=name,
                canary=token,
                response_preview=response[:200],
            )
            leak_detected = True
            break

    if leak_detected:
        return OutputGuardResult(
            text=FALLBACK_RESPONSE,
            was_modified=True,
            leak_detected=True,
            sensitive_detected=False,
            was_truncated=False,
        )

    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(response):
            logger.critical(
                "sensitive_data_in_response",
                pattern=pattern.pattern[:40],
                response_preview=response[:200],
            )
            sensitive_detected = True
            break

    if sensitive_detected:
        return OutputGuardResult(
            text=FALLBACK_RESPONSE,
            was_modified=True,
            leak_detected=False,
            sensitive_detected=True,
            was_truncated=False,
        )

    text = response
    if len(text) > MAX_RESPONSE_LENGTH:
        text = text[:MAX_RESPONSE_LENGTH].rsplit(" ", 1)[0] + "..."
        was_truncated = True
        was_modified = True

    return OutputGuardResult(
        text=text,
        was_modified=was_modified,
        leak_detected=False,
        sensitive_detected=False,
        was_truncated=was_truncated,
    )
