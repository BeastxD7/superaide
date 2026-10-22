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
    # Superaide AI credits: owners may choose to use the platform's own Claude key instead of bringing
    # theirs. Off by default. The key is PLATFORM_ANTHROPIC_API_KEY, else ANTHROPIC_API_KEY; it is
    # never shown, logged or stored in an owner's vault, and only used for owners who opted in.
    platform_ai_enabled: bool = False
    # Accounts (comma-separated emails) whose Ava uses this server's ANTHROPIC_API_KEY as its own key, even
    # when hosted: the operator's own Ava. Normal per-instance budget, not the credits allowance or the
    # platform cap. Emails aren't verified, so only list accounts that already exist.
    platform_key_owner_emails: str = ""
    platform_anthropic_api_key: str = Field(default="", exclude=True)  # never serialised
    platform_model: str = "claude-haiku-5-5"  # every role runs on this model when credits pay
    # Each owner's share of the credits, rolling 24 hours and 30 days, inside PLATFORM_DAILY_CAP_USD.
    platform_owner_daily_usd: float = 0.50
    platform_owner_monthly_usd: float = 5.00
    # Whether this instance's owner opted in to the credits. Filled from the database by
    # vault.store.runtime_settings, never from .env.
    use_platform_ai: bool = Field(default=False, exclude=True)
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

    # Google: one OAuth client (Web application) for this deployment serves both "Sign in with Google"
    # and Google Calendar. Set here, it is the platform's client; an owner may still save their own in
    # Connections for their calendar. The refresh token comes from the owner's calendar sign-in and is
    # kept in their instance's vault (from .env only on a self-hosted server).
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    # "Continue with Google" on the log-in and sign-up pages (OpenID Connect, scopes openid email
    # profile). Needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET above. Off: /auth/google/* answer 404.
    google_signin_enabled: bool = False
    # Google Calendar push (events.watch): Google calls this server when the owner's calendar changes,
    # so meeting prep notices new or moved meetings at once instead of at the next 30-minute scan
    # (which keeps running as the fallback). Needs a public https address (PUBLIC_BASE_URL).
    google_calendar_push: bool = False
    # Public https address of this API as Google and other services reach it (e.g. https://ava.example.com,
    # whose /webhooks/* goes to the API). Falls back to TELEGRAM_WEBHOOK_BASE_URL.
    public_base_url: str = ""
    # E2E only: a fake Google at this address stands in for accounts.google.com, used only while
    # ENABLE_SIMULATOR is on. Never set it on a real server.
    google_fake_base_url: str = ""

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

    # "Ask my Ava" link (docs/research/AGI era growth ideas.md, idea 1). Off: /p/ask/* and
    # /app/ask-link/* answer 404 (owner routes still 401/403 first). On: an owner can publish a
    # pseudonymous /a/<handle> page where anyone can leave a request. No model call for the visitor.
    ask_link_enabled: bool = False
    # Signs the public form's token (time-to-submit and same-form check). Blank: a random key per
    # process, so a restart only means a visitor reloads the page.
    ask_form_secret: str = ""
    ask_min_seconds: float = 3.0  # a form sent faster than this is a bot; it is quietly dropped
    ask_token_max_age_s: int = 2 * 3600
    ask_per_ip_per_hour: int = 5
    ask_per_handle_per_hour: int = 20
    ask_per_handle_per_day: int = 100

    # Web push to the owner's phone (ADR 0011). Off: /app/push/* answers 404 and no VAPID keys are
    # made. On: the first use generates one VAPID key pair for this deployment, kept in the vault.
    push_enabled: bool = False
    vapid_subject: str = "mailto:admin@localhost"  # who push services contact about this sender
    # Every encrypted push is padded to this many plaintext bytes, so its size reveals nothing.
    push_pad_bytes: int = 1024
    # What a lock screen may show for a new device: none ("Ava / Needs you") | sender | reason.
    push_default_preview: str = "none"
    push_ttl_seconds: int = 180  # a push that arrives after the ladder moved on is useless
    push_max_attempts: int = 3  # for 429 and 5xx answers, inside the TTL

    # User accounts and sessions
    session_cookie_name: str = "superaide_session"
    session_cookie_secure: bool = False  # set true when served over HTTPS
    session_days: int = 30  # sliding: counted from the last time the session was used
    login_max_failures: int = 5  # per account, then locked
    login_lock_minutes: int = 15
    login_failures_per_ip: int = 20  # per 15 minutes
    signups_per_ip_per_hour: int = 10
    # Anyone who signs up can hire their own Ava, with its own memory, people, connections and spend
    # (ADR 0009; the owner's decision, 2026-10-22). False is single-owner mode: the first account to hire
    # Ava is its only owner and later hires are refused (a self-hosted server for one person).
    allow_many_owners: bool = True
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
