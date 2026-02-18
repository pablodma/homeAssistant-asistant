"""Application settings."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "HomeAI Assistant"
    app_env: Literal["development", "production"] = "development"
    
    # DEPRECATED: default_tenant_id is no longer used for processing messages.
    # Tenant is now resolved dynamically from phone number.
    # This is kept for backward compatibility with existing configuration.
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"

    # WhatsApp Business API
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str
    whatsapp_access_token: str
    whatsapp_app_secret: str = ""

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4.1-mini"  # Sub-agents (finance, calendar, etc.)
    openai_router_model: str = "gpt-4.1-nano"  # Router agent (fast classification)
    whisper_model: str = "whisper-1"

    # Anthropic (used by QA and Prompt Improver agents)
    anthropic_api_key: str = ""
    qa_model: str = "claude-opus-4-6"  # QA Agent (Quality Control)
    qa_review_model: str = "claude-opus-4-6"  # Prompt Improver (Mejora Continua)
    qa_review_max_improvements: int = 3
    qa_review_cooldown_hours: int = 24
    qa_review_min_issues: int = 2

    # GitHub API (for prompt editing by QA Reviewer)
    github_token: str = ""
    github_repo: str = "pablodma/homeAssistant-asistant"
    github_branch: str = "main"

    # Backend API
    backend_api_url: str = "http://localhost:8000"
    backend_api_key: str = ""

    # Shared secret for /internal/* endpoints (backend -> bot communication)
    internal_api_secret: str = ""

    # Database
    database_url: str

    # CORS (only needed for /docs in development; the bot has no browser clients)
    cors_origins: str = "http://localhost:8000"

    # Rate Limiting
    max_messages_per_minute: int = 20

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
