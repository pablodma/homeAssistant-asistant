"""QA Review Cron.

Runs weekly (Monday 3AM UTC) to automatically review quality issues
and improve agent prompts via the PromptImprover pipeline.

Schedule: configure in Railway cron or call via internal API.
Command: python -m src.app.crons.run_qa_review
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from ..config import get_settings
from ..config.database import get_pool
from ..services.qa_reviewer import QABatchReviewer

logger = structlog.get_logger()


async def run_qa_review_all() -> dict:
    """Run QA review for all active tenants.

    Checks each tenant for unresolved issues, respects cooldown and
    min_issues thresholds, and runs the PromptImprover pipeline.

    Returns:
        Stats dict with tenants_checked, tenants_reviewed, tenants_skipped, errors.
    """
    settings = get_settings()

    if not settings.qa_review_cron_enabled:
        logger.info("QA review cron disabled, skipping")
        return {"skipped": True, "reason": "cron disabled"}

    pool = await get_pool()
    reviewer = QABatchReviewer()
    lookback_days = settings.qa_review_cron_lookback_days

    stats = {
        "tenants_checked": 0,
        "tenants_reviewed": 0,
        "tenants_skipped": 0,
        "improvements_applied": 0,
        "errors": 0,
    }

    # Get active tenants
    tenant_rows = await pool.fetch(
        "SELECT id FROM tenants WHERE active = true ORDER BY created_at"
    )
    tenant_ids = [str(row["id"]) for row in tenant_rows]

    logger.info(
        "QA review cron started",
        tenants_found=len(tenant_ids),
        lookback_days=lookback_days,
    )

    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    for tenant_id in tenant_ids:
        stats["tenants_checked"] += 1

        try:
            # Check if tenant has enough unresolved issues
            issue_count = await pool.fetchval(
                """
                SELECT COUNT(*) FROM quality_issues
                WHERE tenant_id = $1
                  AND is_resolved = false
                  AND created_at >= $2
                """,
                tenant_id,
                since,
            )

            if issue_count < settings.qa_review_min_issues:
                logger.debug(
                    "Tenant has too few issues, skipping",
                    tenant_id=tenant_id,
                    issue_count=issue_count,
                    min_required=settings.qa_review_min_issues,
                )
                stats["tenants_skipped"] += 1
                continue

            # Check cooldown: skip if there's a completed cycle in last 24h
            recent_cycle = await pool.fetchval(
                """
                SELECT COUNT(*) FROM qa_review_cycles
                WHERE tenant_id = $1
                  AND status = 'completed'
                  AND completed_at >= NOW() - INTERVAL '24 hours'
                """,
                tenant_id,
            )

            if recent_cycle > 0:
                logger.debug(
                    "Tenant has recent review cycle, skipping",
                    tenant_id=tenant_id,
                )
                stats["tenants_skipped"] += 1
                continue

            # Run the review
            logger.info(
                "Running QA review for tenant",
                tenant_id=tenant_id,
                issue_count=issue_count,
            )

            result = await reviewer.run_review(
                tenant_id=tenant_id,
                triggered_by="cron",
                days=lookback_days,
            )

            stats["tenants_reviewed"] += 1
            stats["improvements_applied"] += result.get("improvements_applied", 0)

            logger.info(
                "QA review completed for tenant",
                tenant_id=tenant_id,
                cycle_id=result.get("cycle_id"),
                issues_analyzed=result.get("issues_analyzed"),
                improvements_applied=result.get("improvements_applied"),
            )

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                "QA review cron failed for tenant",
                tenant_id=tenant_id,
                error=str(e),
            )

    logger.info("QA review cron finished", **stats)
    return stats


if __name__ == "__main__":
    asyncio.run(run_qa_review_all())
