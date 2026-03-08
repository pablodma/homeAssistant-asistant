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
    openai_guardrails_model: str = "gpt-4.1-nano"
    whisper_model: str = "whisper-1"

    # Anthropic (used by QA, Prompt Improver, and sub-agents)
    anthropic_api_key: str = ""
    anthropic_subagent_model: str = "claude-haiku-4-5-20251001"

    # Per-agent model provider for gradual rollout / rollback ("openai" | "anthropic")
    finance_model_provider: str = "anthropic"
    calendar_model_provider: str = "anthropic"
    shopping_model_provider: str = "anthropic"
    vehicle_model_provider: str = "anthropic"
    subscription_model_provider: str = "anthropic"
    qa_model: str = "claude-sonnet-4-20250514"
    qa_review_model: str = "claude-opus-4-6"  # deprecated: use the split models below
    qa_review_analysis_model: str = "claude-sonnet-4-20250514"  # Step 1: issue analysis (cheap)
    qa_review_improvement_model: str = "claude-opus-4-6"  # Step 2: prompt generation (expensive)
    qa_review_max_improvements: int = 3
    qa_review_cooldown_hours: int = 24
    qa_review_min_issues: int = 2

    # QA Review Cron
    qa_review_cron_enabled: bool = True
    qa_review_cron_lookback_days: int = 14

    # QA Review Auto-trigger by threshold
    qa_review_auto_trigger_enabled: bool = False
    qa_review_auto_trigger_threshold: int = 10

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
