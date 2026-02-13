"""Internal API router for QA Review operations.

These endpoints are meant to be triggered via curl / Railway CLI,
NOT from the admin frontend. They run heavy AI workloads (Claude Opus).

Usage:
    # Review ALL active tenants:
    curl -X POST https://homeai-assis-production.up.railway.app/internal/qa-review/all \
        -H "Content-Type: application/json" \
        -d '{"triggered_by": "admin@email.com", "days": 30}'

    # Review a specific tenant:
    curl -X POST https://homeai-assis-production.up.railway.app/internal/qa-review \
        -H "Content-Type: application/json" \
        -d '{"tenant_id": "...", "triggered_by": "admin@email.com", "days": 30}'
"""

import traceback
from typing import Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ..config.database import get_pool
from ..services.qa_reviewer import QABatchReviewer

logger = structlog.get_logger()

router = APIRouter(prefix="/internal", tags=["internal"])


# =====================================================
# REQUEST / RESPONSE SCHEMAS
# =====================================================


class QAReviewRequest(BaseModel):
    """Request body for triggering a QA review for a single tenant."""

    tenant_id: str = Field(..., description="UUID of the tenant to review")
    triggered_by: str = Field(
        default="cli",
        description="Email or identifier of who triggered the review",
    )
    days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Number of days back to analyze",
    )


class QAReviewAllRequest(BaseModel):
    """Request body for triggering a QA review for ALL active tenants."""

    triggered_by: str = Field(
        default="cli",
        description="Email or identifier of who triggered the review",
    )
    days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Number of days back to analyze",
    )


class QAReviewResponse(BaseModel):
    """Response for a triggered QA review."""

    cycle_id: str
    status: str
    message: str


class QAReviewAllResponse(BaseModel):
    """Response for triggering reviews across all tenants."""

    status: str
    tenants_count: int
    tenant_ids: list[str]
    message: str


# =====================================================
# SINGLE TENANT ENDPOINTS
# =====================================================


@router.post("/qa-review", response_model=QAReviewResponse)
async def trigger_qa_review(
    request: QAReviewRequest,
    background_tasks: BackgroundTasks,
):
    """Trigger a QA review cycle for a single tenant.

    This endpoint starts a background task that:
    1. Fetches unresolved quality issues
    2. Analyzes them with Claude Opus
    3. Generates and applies prompt improvements via GitHub

    The response returns immediately with the cycle ID.
    Use the admin panel history view or DB to check progress.
    """
    try:
        reviewer = QABatchReviewer()

        # Run in background so the HTTP response returns quickly
        background_tasks.add_task(
            _run_review_safe,
            reviewer=reviewer,
            tenant_id=request.tenant_id,
            triggered_by=request.triggered_by,
            days=request.days,
        )

        return QAReviewResponse(
            cycle_id="pending",
            status="started",
            message=(
                f"QA review started for tenant {request.tenant_id}. "
                f"Analyzing last {request.days} days. Check admin panel for results."
            ),
        )

    except Exception as e:
        logger.error(
            "Failed to start QA review",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/qa-review/sync", response_model=None)
async def trigger_qa_review_sync(request: QAReviewRequest):
    """Trigger a QA review cycle synchronously (waits for completion).

    Use this for debugging or when you need the full result immediately.
    WARNING: This can take several minutes depending on the number of issues.
    """
    try:
        reviewer = QABatchReviewer()
        result = await reviewer.run_review(
            tenant_id=request.tenant_id,
            triggered_by=request.triggered_by,
            days=request.days,
        )
        return result

    except Exception as e:
        logger.error(
            "QA review sync failed",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# ALL TENANTS ENDPOINTS
# =====================================================


@router.post("/qa-review/all", response_model=QAReviewAllResponse)
async def trigger_qa_review_all(
    request: QAReviewAllRequest,
    background_tasks: BackgroundTasks,
):
    """Trigger a QA review for ALL active tenants.

    Queries the tenants table for active tenants, then starts a background
    review for each one sequentially (to avoid overwhelming the LLM API).

    The response returns immediately with the list of tenant IDs queued.
    """
    try:
        tenant_ids = await _get_active_tenant_ids()

        if not tenant_ids:
            return QAReviewAllResponse(
                status="no_tenants",
                tenants_count=0,
                tenant_ids=[],
                message="No hay tenants activos en la base de datos.",
            )

        reviewer = QABatchReviewer()

        # Run all reviews sequentially in a single background task
        background_tasks.add_task(
            _run_review_all_safe,
            reviewer=reviewer,
            tenant_ids=tenant_ids,
            triggered_by=request.triggered_by,
            days=request.days,
        )

        return QAReviewAllResponse(
            status="started",
            tenants_count=len(tenant_ids),
            tenant_ids=tenant_ids,
            message=(
                f"QA review started for {len(tenant_ids)} tenant(s). "
                f"Analyzing last {request.days} days. Check admin panel for results."
            ),
        )

    except Exception as e:
        logger.error(
            "Failed to start QA review for all tenants",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# HELPER FUNCTIONS
# =====================================================


async def _get_active_tenant_ids() -> list[str]:
    """Get all active tenant IDs from the database."""
    pool = await get_pool()
    rows = await pool.fetch("SELECT id FROM tenants WHERE active = true ORDER BY created_at")
    return [str(row["id"]) for row in rows]


async def _run_review_safe(
    reviewer: QABatchReviewer,
    tenant_id: str,
    triggered_by: str,
    days: int,
) -> None:
    """Run QA review with error handling (for background tasks)."""
    try:
        result = await reviewer.run_review(
            tenant_id=tenant_id,
            triggered_by=triggered_by,
            days=days,
        )
        logger.info(
            "Background QA review completed",
            tenant_id=tenant_id,
            cycle_id=result.get("cycle_id"),
            issues_analyzed=result.get("issues_analyzed"),
            improvements_applied=result.get("improvements_applied"),
        )
    except Exception as e:
        logger.error(
            "Background QA review failed",
            tenant_id=tenant_id,
            error=str(e),
            traceback=traceback.format_exc(),
        )


async def _run_review_all_safe(
    reviewer: QABatchReviewer,
    tenant_ids: list[str],
    triggered_by: str,
    days: int,
) -> None:
    """Run QA review for all tenants sequentially with error handling.

    Runs one at a time to avoid overwhelming the Claude API with
    concurrent requests (each review can use significant tokens).
    """
    total = len(tenant_ids)
    completed = 0
    failed = 0

    logger.info(
        "Starting QA review for all tenants",
        total_tenants=total,
        triggered_by=triggered_by,
        days=days,
    )

    for i, tenant_id in enumerate(tenant_ids, 1):
        try:
            logger.info(
                "Running QA review for tenant",
                tenant_id=tenant_id,
                progress=f"{i}/{total}",
            )
            result = await reviewer.run_review(
                tenant_id=tenant_id,
                triggered_by=triggered_by,
                days=days,
            )
            completed += 1
            logger.info(
                "QA review completed for tenant",
                tenant_id=tenant_id,
                cycle_id=result.get("cycle_id"),
                issues_analyzed=result.get("issues_analyzed"),
                improvements_applied=result.get("improvements_applied"),
                progress=f"{i}/{total}",
            )
        except Exception as e:
            failed += 1
            logger.error(
                "QA review failed for tenant",
                tenant_id=tenant_id,
                error=str(e),
                progress=f"{i}/{total}",
                traceback=traceback.format_exc(),
            )

    logger.info(
        "QA review for all tenants finished",
        total=total,
        completed=completed,
        failed=failed,
    )
