"""Input guard for prompt injection detection and message sanitization.

Preprocesses user messages before they reach the LLM.
Flags suspicious patterns but does NOT block messages to avoid false positives.
"""

import re
import unicodedata
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

MAX_MESSAGE_LENGTH = 2000

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignor\w*\s+.{0,30}(previous|above|prior|earlier|antes|anteriores)\s+.{0,20}(instructions|instrucciones|reglas|rules)", re.IGNORECASE),
    re.compile(r"(you are now|act as|pretend to be|roleplay as|ahora sos|actua como|fing[ií] que sos)", re.IGNORECASE),
    re.compile(r"(system prompt|system message|your instructions|show me your prompt|mostr[aá]me tu prompt|tu prompt|tus instrucciones internas)", re.IGNORECASE),
    re.compile(r"(\[INST\]|\[/INST\]|<\|im_start\|>|<\|im_end\|>|<\|system\|>|<\|user\|>|<\|assistant\|>)", re.IGNORECASE),
    re.compile(r"(forget|disregard|override|olvid[aá]te|descart[aá]|ignor[aá]).{0,30}(rules|instructions|behavior|reglas|instrucciones|comportamiento)", re.IGNORECASE),
    re.compile(r"(repet[ií]|repeat|print|write out|escrib[ií]).{0,30}(system|prompt|instrucciones|instructions)", re.IGNORECASE),
    re.compile(r"(DAN|jailbreak|bypass|modo desarrollador|developer mode)", re.IGNORECASE),
]


@dataclass
class InputGuardResult:
    """Result of input guard processing."""
    text: str
    injection_suspected: bool
    matched_patterns: list[str]
    was_truncated: bool


def sanitize_message(text: str) -> InputGuardResult:
    """Sanitize and analyze a user message for injection patterns.

    Does NOT block the message. Only sanitizes and flags.
    The LLM handles behavioral defense via its prompt.
    """
    matched_patterns: list[str] = []
    was_truncated = False

    normalized = unicodedata.normalize("NFKC", text)

    if len(normalized) > MAX_MESSAGE_LENGTH:
        normalized = normalized[:MAX_MESSAGE_LENGTH]
        was_truncated = True

    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(normalized)
        if match:
            matched_patterns.append(match.group(0)[:80])

    injection_suspected = len(matched_patterns) > 0

    if injection_suspected:
        logger.warning(
            "injection_pattern_detected",
            patterns=matched_patterns,
            text_preview=normalized[:100],
        )

    return InputGuardResult(
        text=normalized,
        injection_suspected=injection_suspected,
        matched_patterns=matched_patterns,
        was_truncated=was_truncated,
    )
