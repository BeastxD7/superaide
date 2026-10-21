"""Settings, loaded from environment variables or a .env file."""

from __future__ import annotations

import uuid
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fixed IDs for the single-tenant MVP. Every row carries them so the
# project can become multi-employer later without a data migration.
DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEFAULT_AGENT_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://agent:agent@localhost:5432/agent"

    # Tenancy
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID
    agent_id: uuid.UUID = DEFAULT_AGENT_ID
    agent_name: str = "Ava"

    # Claude API
    anthropic_api_key: str = ""
    model_chat: str = "claude-sonnet-5-5"
    model_triage: str = "claude-haiku-5-5"
    model_deep: str = "claude-opus-5-5"
    # Hard ceiling on total Claude API spend, in US dollars, across the whole build.
    max_spend_usd: float = 10.0
    # Use a scripted fake model instead of the real API (tests, offline demos).
    fake_llm: bool = False

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_signing_secret: str = ""
    slack_team_id: str = ""  # your workspace; users from any other team are external

    # Admin API used by the dashboard
    admin_token: str = Field(default="", description="Bearer token for /api/*")
    dashboard_origin: str = "http://localhost:5173"
    # Dev only: lets the dashboard inject fake messages to try impostor scenarios.
    enable_simulator: bool = False

    # Identity linking
    link_code_ttl_minutes: int = 10
    link_code_max_attempts: int = 5
    link_challenges_per_identity_per_day: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
