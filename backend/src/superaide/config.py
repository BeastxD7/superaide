"""Settings, loaded from environment variables or a .env file."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Literal

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
    # The schema owner, for migrations and `superaide db-app-role` (ADR 0009). When set, DATABASE_URL
    # should be the app's own role (no superuser, no BYPASSRLS), so Row-Level Security applies.
    migration_database_url: str = ""

    @property
    def owner_database_url(self) -> str:
        return self.migration_database_url or self.database_url

    # self_hosted: one owner's own machine (local model servers allowed). hosted: many owners on our
    # servers, so no owner-typed address may reach private, loopback, link-local or metadata ranges.
    deploy_mode: Literal["self_hosted", "hosted"] = "self_hosted"

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
        default_factory=lambda: {"chat": 0.40, "memory": 0.15, "research": 0.25, "triage": 0.05}
    )
    # These limits apply to each hired Ava on its own; an instance may set its own (agent_instance.spend_limits).
    # Platform-wide, per UTC day, for calls paid with Superaide's own model key (ADR 0009). Owners'
    # own keys never draw on it. 0 refuses every platform-paid call (no platform key is offered yet).
    platform_daily_cap_usd: float = 0.0
    # Other model providers the owner connected in the app (preset -> base_url, api_key). Filled from
    # the database by vault.store.runtime_settings, never from .env. A model id "<preset>::<model>"
    # in model_chat/model_triage/model_deep runs on that provider; a bare id runs on Claude.
    llm_providers: dict[str, dict[str, str]] = Field(default_factory=dict, exclude=True)
    # Use a scripted fake model instead of the real API (tests, offline demos).
    fake_llm: bool = False
    # Embeddings for recall (ADR 0008 WP2b): local (on this machine, free, the default) | voyage
    # (Voyage AI's API: private facts leave the machine, so it's opt-in) | fake (tests) | none
    embedder: str = "local"
    embed_model_local: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 50+ languages
    embed_cache_dir: str = ""  # where the local model is kept; default ~/.cache/superaide/models
    voyage_api_key: str = ""
    embed_model_voyage: str = "voyage-4-lite"
    embed_monthly_usd: float = 1.00  # cap on Voyage spend in any 30 days (local costs nothing)
    # Let Ava search and read the web for the owner (costs US$0.01 a search, from the research budget)
    web_tools_enabled: bool = True
    # Web search for models without a built-in one (Brave Search API). What one search costs on your
    # plan, charged against the cap before each search.
    brave_search_api_key: str = ""
    web_search_usd_per_call: float = 0.01

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    # Public https address of this server (e.g. https://ava.example.com). When set, each owner's bot gets a
    # webhook at <this>/webhooks/telegram/<endpoint> with its own secret, instead of polling (ADR 0009 T4).
    telegram_webhook_base_url: str = ""

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

    # Triage and escalation (ADR 0010). Off: every known non-owner gets a model reply, as before.
    # On: each of their messages also gets a recorded decision (handle, hold, interrupt, defer or
    # verify), and an interrupt escalates to the owner until acknowledged. What the sender is told
    # doesn't change until the pipeline hook (TR5) lands.
    triage_enabled: bool = False

    # User accounts and sessions
    session_cookie_name: str = "superaide_session"
    session_cookie_secure: bool = False  # set true when served over HTTPS
    session_days: int = 30  # sliding: counted from the last time the session was used
    login_max_failures: int = 5  # per account, then locked
    login_lock_minutes: int = 15
    login_failures_per_ip: int = 20  # per 15 minutes
    signups_per_ip_per_hour: int = 10
    # Let more than one account hire Ava, each getting their own instance (ADR 0009). Off until the
    # per-instance runtime, channels, jobs and memory scoping (T3-T7) are in: with it on before then,
    # owners would share one set of connections.
    allow_many_owners: bool = False
    # Hiring needs a verified email address. Off by default until an email provider is chosen.
    require_verified_email: bool = False
    verify_link_hours: int = 48
    reset_link_minutes: int = 30
    email_links_per_account_per_hour: int = 5

    # Email (verification and password-reset links). "log" writes each email to the server log
    # (development); "smtp" sends through any SMTP server or provider.
    mail_backend: str = "log"  # log | smtp
    mail_from: str = "Superaide <no-reply@localhost>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"  # starttls | ssl | none
    smtp_timeout_seconds: float = 15.0

    # Identity linking
    link_code_ttl_minutes: int = 10
    link_code_max_attempts: int = 5
    link_challenges_per_identity_per_day: int = 3
    # A linked Telegram/Slack account silent this long must be confirmed again in the app (0 = never).
    link_reconfirm_after_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
