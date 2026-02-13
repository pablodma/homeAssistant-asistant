"""Internal API router for QA Review operations.

These endpoints are meant to be triggered via curl / Railway CLI,
NOT from the admin frontend. They run heavy AI workloads (Claude Opus).

Usage:
    curl -X POST https://homeai-assis.up.railway.app/internal/qa-review \
        -H "Content-Type: application/json" \
        -d '{"tenant_id": "...", "triggered_by": "admin@email.com", "days": 30}'
"""

import traceback

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ..services.qa_reviewer import QABatchReviewer

logger = structlog.get_logger()

router = APIRouter(prefix="/internal", tags=["internal"])


class QAReviewRequest(BaseModel):
    """Request body for triggering a QA review."""

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


class QAReviewResponse(BaseModel):
    """Response for a triggered QA review."""

    cycle_id: str
    status: str
    message: str


@router.post("/qa-review", response_model=QAReviewResponse)
async def trigger_qa_review(
    request: QAReviewRequest,
    background_tasks: BackgroundTasks,
):
    """Trigger a QA review cycle.

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
            cycle_id=result.get("cycle_id"),
            issues_analyzed=result.get("issues_analyzed"),
            improvements_applied=result.get("improvements_applied"),
        )
    except Exception as e:
        logger.error(
            "Background QA review failed",
            error=str(e),
            traceback=traceback.format_exc(),
        )
