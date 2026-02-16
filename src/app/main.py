"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .config.database import close_pool, get_pool
from .routers.internal import router as internal_router
from .whatsapp.webhook import router as webhook_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    settings = get_settings()
    logger.info(
        "Starting application",
        app_name=settings.app_name,
        env=settings.app_env,
    )

    # #region agent log
    import os
    db_url = settings.database_url
    db_host = db_url.split("@")[1].split("/")[0] if "@" in db_url else "unknown"
    logger.info(
        "[DEBUG] startup_env_check",
        app_env=settings.app_env,
        db_host=db_host,
        backend_url=settings.backend_api_url,
        railway_env=os.environ.get("RAILWAY_ENVIRONMENT_NAME", "not_set"),
        hypothesisId="H1",
    )
    # #endregion

    # Initialize database pool
    try:
        await get_pool()
        logger.info("Database pool initialized")
        # #region agent log
        logger.info("[DEBUG] db_pool_created_ok", hypothesisId="H1")
        # #endregion
    except Exception as e:
        logger.error("Database connection failed", error=str(e))
        # #region agent log
        logger.error("[DEBUG] db_pool_creation_failed", error=str(e), db_host=db_host, hypothesisId="H1")
        # #endregion
        logger.warning("App will start but database operations will fail")

    yield

    # Shutdown
    try:
        await close_pool()
        logger.info("Database pool closed")
    except Exception:
        pass


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="WhatsApp Bot Service for HomeAI Assistant",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "service": "homeai-assis"}

    # Root
    @app.get("/")
    async def root():
        """Root endpoint."""
        return {
            "service": "HomeAI Assistant Bot",
            "version": "0.1.0",
            "status": "running",
        }

    # WhatsApp webhook router
    app.include_router(webhook_router)

    # Internal API router (QA Review trigger via curl/CLI)
    app.include_router(internal_router)

    return app


# Application instance
app = create_app()
