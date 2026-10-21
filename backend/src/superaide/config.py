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
    model_chat: str = "claude-haiku-5-5"  # Sonnet 5.5 later; Haiku keeps development inside the cap
    model_triage: str = "claude-haiku-5-5"
    model_deep: str = "claude-opus-5-5"
    # Hard ceiling on total Claude API spend, in US dollars, across the whole build.
    max_spend_usd: float = 10.0
    # Rolling limits inside the lifetime cap, so one busy day or one feature can't spend it all.
    budget_daily_usd: float = 0.50  # any 24 hours
    budget_monthly_usd: float = 5.00  # any 30 days
    # Per purpose bucket, any 24 hours (chat replies, memory reading, web research).
    budget_daily_by_bucket: dict[str, float] = Field(
        default_factory=lambda: {"chat": 0.40, "memory": 0.15, "research": 0.25}
    )
    # Use a scripted fake model instead of the real API (tests, offline demos).
    fake_llm: bool = False
    # Let Ava search and read the web for the owner (costs US$0.01 a search, from the research budget)
    web_tools_enabled: bool = True

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # Google (Calendar): the owner's own OAuth client, and the refresh token from signing in
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_signing_secret: str = ""
    slack_team_id: str = ""  # your workspace; users from any other team are external

    # Admin API used by the dashboard
    admin_token: str = Field(default="", description="Bearer token for /api/*")
    # The React frontend (web/) runs on its own dev server and calls this API from the browser.
    frontend_origin: str = "http://localhost:3000"
    # Dev only: lets the dashboard inject fake messages to try impostor scenarios.
    enable_simulator: bool = False
    # The interactive API docs (/docs, /redoc, /openapi.json). Turn off on a public server.
    enable_api_docs: bool = True

    # Run the Telegram and Slack listeners inside the API (follows connections made in the app)
    run_channels_in_api: bool = True
    # Run background jobs (reminders, follow-ups, digests) inside the API process
    run_jobs_in_api: bool = True

    # User accounts and sessions
    session_cookie_name: str = "superaide_session"
    session_cookie_secure: bool = False  # set true when served over HTTPS
    session_days: int = 30  # sliding: counted from the last time the session was used
    login_max_failures: int = 5  # per account, then locked
    login_lock_minutes: int = 15
    login_failures_per_ip: int = 20  # per 15 minutes
    signups_per_ip_per_hour: int = 10

    # Identity linking
    link_code_ttl_minutes: int = 10
    link_code_max_attempts: int = 5
    link_challenges_per_identity_per_day: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
