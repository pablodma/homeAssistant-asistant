"""Rolling Summary Generator Cron.

Runs 2x/day to generate LLM-based conversation summaries.
Summaries are stored in chat_sessions.conversation_summary.

Schedule: call generate_all_summaries() from a scheduler or Railway cron.

The cron reads conversations that:
  - Have more than MIN_MESSAGES_TO_SUMMARIZE messages
  - Were last summarized more than SUMMARY_REFRESH_HOURS ago (or never)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from openai import AsyncOpenAI

from ..config import get_settings
from ..config.database import get_pool

logger = structlog.get_logger()

MIN_MESSAGES_TO_SUMMARIZE = 5
SUMMARY_REFRESH_HOURS = 12
MAX_SESSIONS_PER_RUN = 100
SUMMARY_MODEL = "gpt-4.1-mini"

SUMMARY_SYSTEM_PROMPT = """Sos un asistente que genera resumenes concisos de conversaciones de hogar.

Dado el historial de conversacion, genera un resumen narrativo en espanol que capture:
- Los gastos o presupuestos mencionados recientemente
- Los eventos o citas proximas discutidas
- Las listas de compras o vehiculos mencionados
- El tono general y preferencias del usuario
- Cualquier contexto importante para futuras conversaciones

El resumen debe ser claro, en tercera persona, maximo 200 palabras.
Comenzar con "El usuario..." o "La conversacion..."
"""


async def generate_summary_for_session(
    session_key: str,
    messages: list[dict],
    client: AsyncOpenAI,
) -> str | None:
    """Generate a rolling summary for a single conversation session."""
    if len(messages) < MIN_MESSAGES_TO_SUMMARIZE:
        return None

    # Build conversation text for the LLM
    conversation_text = "\n".join(
        f"[{msg['role'].upper()}]: {msg['content'][:300]}"
        for msg in messages[-50:]  # Last 50 messages max to avoid token overflow
    )

    try:
        response = await client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Genera un resumen de esta conversacion:\n\n{conversation_text}",
                },
            ],
            temperature=0.3,
            max_completion_tokens=400,
        )
        return (response.choices[0].message.content or "").strip() or None
    except Exception as exc:
        logger.error(
            "Failed to generate summary for session",
            session_key=session_key,
            error=str(exc),
        )
        return None


async def generate_all_summaries() -> dict[str, int]:
    """Generate summaries for all stale conversation sessions.

    Returns a dict with counts: {"processed": N, "updated": N, "skipped": N, "errors": N}
    """
    settings = get_settings()
    pool = await get_pool()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=SUMMARY_REFRESH_HOURS)
    stats = {"processed": 0, "updated": 0, "skipped": 0, "errors": 0}

    async with pool.acquire() as conn:
        # Get sessions that need a summary refresh
        sessions = await conn.fetch(
            """
            SELECT cs.session_key, cs.id
            FROM chat_sessions cs
            WHERE (cs.summary_updated_at IS NULL OR cs.summary_updated_at < $1)
            ORDER BY cs.summary_updated_at NULLS FIRST
            LIMIT $2
            """,
            cutoff,
            MAX_SESSIONS_PER_RUN,
        )

        logger.info("Starting rolling summary generation", session_count=len(sessions))

        for session in sessions:
            stats["processed"] += 1
            session_key = session["session_key"]

            try:
                # Get messages for this session
                messages = await conn.fetch(
                    """
                    SELECT role, content
                    FROM chat_messages
                    WHERE session_key = $1
                    ORDER BY created_at ASC
                    """,
                    session_key,
                )

                if len(messages) < MIN_MESSAGES_TO_SUMMARIZE:
                    stats["skipped"] += 1
                    continue

                messages_list = [{"role": m["role"], "content": m["content"]} for m in messages]
                summary = await generate_summary_for_session(session_key, messages_list, client)

                if summary:
                    await conn.execute(
                        """
                        UPDATE chat_sessions
                        SET conversation_summary = $1,
                            summary_updated_at = NOW()
                        WHERE session_key = $2
                        """,
                        summary,
                        session_key,
                    )
                    stats["updated"] += 1
                    logger.debug("Summary updated", session_key=session_key)
                else:
                    stats["skipped"] += 1

            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Error generating summary for session",
                    session_key=session_key,
                    error=str(exc),
                )

    logger.info("Rolling summary generation complete", **stats)
    return stats


if __name__ == "__main__":
    asyncio.run(generate_all_summaries())
