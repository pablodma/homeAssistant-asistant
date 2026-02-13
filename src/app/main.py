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

    # Initialize database pool
    try:
        await get_pool()
        logger.info("Database pool initialized")
    except Exception as e:
        logger.error("Database connection failed", error=str(e))
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
