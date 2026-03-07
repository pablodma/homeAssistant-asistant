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
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"

    # WhatsApp Business API
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str
    whatsapp_access_token: str
    whatsapp_app_secret: str = ""

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4.1-mini"
    openai_router_model: str = "gpt-4.1-nano"
    orchestrator_finalizer_enabled: bool = False
    orchestrator_finalize_on_multi_agent_only: bool = True
    orchestrator_finalizer_model: str = "gpt-4.1-nano"
    whisper_model: str = "whisper-1"

    # Anthropic (used by QA and Prompt Improver agents)
    anthropic_api_key: str = ""
    qa_model: str = "claude-sonnet-4-20250514"
    qa_review_model: str = "claude-opus-4-6"
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

    # Frontend URL
    frontend_url: str = "http://localhost:3000"

    # Shared secret for /internal/* endpoints
    internal_api_secret: str = ""

    # Database
    database_url: str

    # CORS
    cors_origins: str = "http://localhost:8000"

    # Rate Limiting
    max_messages_per_minute: int = 20

    # Redis (optional — used by circuit breaker and rate limiter)
    redis_url: str = ""

    # LangGraph checkpointing: "memory" (dev) or "postgres" (prod)
    langgraph_checkpointing: str = "memory"

    # Circuit Breaker (LLM)
    breaker_fail_threshold: int = 5
    breaker_open_seconds: int = 60
    breaker_half_open_max_calls: int = 3

    # Langfuse (LLM observability)
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = False

    # Response Guardrails (post-LLM security checks)
    final_security_check_enabled: bool = False
    injection_threshold: float = 0.8
    coherence_threshold: float = 0.6

    # Hybrid Memory
    immediate_memory_max_messages: int = 10
    immediate_memory_hours: int = 48

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.app_env == "production"

    @property
    def redis_available(self) -> bool:
        """Check if Redis URL is configured."""
        return bool(self.redis_url)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
