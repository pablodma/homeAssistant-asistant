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
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"

    # WhatsApp Business API
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str
    whatsapp_access_token: str

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4.1-mini"

    # Backend API
    backend_api_url: str = "https://homeassistant-backend-production.up.railway.app"
    backend_api_key: str = ""

    # Database
    database_url: str

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
