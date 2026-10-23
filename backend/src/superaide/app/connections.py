"""/app/connections: connect Telegram, Slack and Google Calendar from the app.

Owner only. A credential is checked with its service, encrypted, saved, and
applied at once (the agent and listeners are rebuilt). Responses never contain
a credential, only a masked hint.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from superaide.app.routes import owner_person_id, telegram_username
from superaide.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.channels import health
from superaide.config import get_settings
from superaide.connectors import google
from superaide.db.session import session_scope
from superaide.tenancy import endpoints
from superaide.tenancy.context import InstanceScope, scope_or_default, use_instance
from superaide.tenancy.models import PlatformState
from superaide.vault import check, store
from superaide.vault.crypto import master_key
from superaide.vault.store import runtime_settings

log = logging.getLogger(__name__)


def _pending_key(state: str) -> str:
    return f"google_pending:{hashlib.sha256(state.encode()).hexdigest()[:40]}"  # fits platform_state.key


def google_redirect_uri() -> str:
    # The browser comes back through the web app's /api proxy, so the session cookie is sent.
    return f"{get_settings().frontend_origin.rstrip('/')}/api/app/connections/google/callback"


BACK_PAGES = {"connections": "/dashboard/connections", "skills": "/dashboard/capabilities"}


def begin_google(user_id: uuid.UUID, *, events: bool, back: str = "connections") -> str:
    """Google's consent URL for the owner in scope: calendar.readonly, plus calendar.events when
    calendar actions are on (asked for incrementally, with include_granted_scopes). Raises
    HTTPException 409 when no OAuth client is set up."""
    settings = runtime_settings()
    if not (settings.google_client_id and settings.google_client_secret):
        raise HTTPException(409, "Save your Google OAuth client first.")
    verifier, challenge = google.pkce_pair()
    state = secrets.token_urlsafe(24)
    scope = scope_or_default()
    scopes = [*google.SCOPES, google.EVENTS_SCOPE] if events else list(google.SCOPES)
    with session_scope() as s:
        # Kept platform-wide under the state's hash: the callback arrives without a session and must
        # find which owner's Ava (ADR 0009) the sign-in belongs to. Only the latest start per instance counts.
        for old in s.scalars(select(PlatformState).where(PlatformState.key.like("google_pending:%"))).all():
            if (old.value or {}).get("agent_id") == str(scope.agent_id):
                s.delete(old)
        s.add(
            PlatformState(
                key=_pending_key(state),
                value={
                    "verifier": verifier, "user_id": str(user_id), "at": datetime.now(UTC).isoformat(),
                    "agent_id": str(scope.agent_id), "tenant_id": str(scope.tenant_id), "scopes": scopes,
                    "back": back if back in BACK_PAGES else "connections",
                },
            )
        )  # fmt: skip
    return google.auth_url(settings.google_client_id, google_redirect_uri(), state, challenge, scopes)


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=15)


class TelegramBody(BaseModel):
    bot_token: str = Field(min_length=10, max_length=100)


class VoiceKeyBody(BaseModel):
    api_key: str = Field(min_length=10, max_length=300)


class GoogleClientBody(BaseModel):
    client_id: str = Field(min_length=10, max_length=200)
    client_secret: str = Field(min_length=10, max_length=200)


class SlackBody(BaseModel):
    bot_token: str = Field(min_length=10, max_length=300)
    app_token: str = Field(min_length=10, max_length=300)


GUIDES = {
    "telegram": {
        "url": "https://t.me/BotFather",
        "steps": [
            "Open @BotFather in Telegram and send /newbot.",
            "Pick a name (e.g. Ava for Shashank) and a username ending in bot.",
            "BotFather replies with a token like 123456789:AAE…. Copy it.",
            "Paste it here. Then open your bot once so it knows it's you.",
        ],
    },
    "google": {
        "url": "https://console.cloud.google.com/apis/credentials",
        "steps": [
            "In Google Cloud, pick or create a project and enable the Google Calendar API.",
            "Under Google Auth Platform, set up Branding and Audience (External, Testing) and add your own "
            "Google account as a test user.",
            "Under Data Access, add the calendar.readonly scope (and calendar.events if you'll let Ava change events).",
            "Create a client of type Web application with the redirect URI below. The same client can serve "
            "Sign in with Google too: add its redirect URI as well.",
            "Paste the client ID and secret here, then press Connect Google Calendar.",
            "While the app is in Testing, Google ends calendar access after 7 days: reconnect then, or publish "
            "the app.",
        ],
    },
    "deepgram": {
        "url": "https://console.deepgram.com/",
        "steps": [
            "Sign up at Deepgram (new accounts get free credit) and open the console.",
            "Under API Keys, create a key with the Member role and copy it.",
            "Paste it here. Ava then hears you on the voice call with Deepgram Nova-3, and can speak with "
            "its Aura voices if you don't add ElevenLabs.",
        ],
    },
    "elevenlabs": {
        "url": "https://elevenlabs.io/app/settings/api-keys",
        "steps": [
            "Sign in to ElevenLabs and open Settings, then API Keys.",
            "Create a key with Text to Speech access (you can leave the rest off) and copy it.",
            "Paste it here. Ava then speaks with a natural ElevenLabs voice on the voice call.",
        ],
    },
    "slack": {
        "url": "",  # filled with the pre-filled app manifest link
        "steps": [
            "Open the link: Slack creates your own Ava app with everything set up.",
            "Click Install to Workspace and allow it.",
            "Copy the Bot User OAuth Token (xoxb-…) from OAuth & Permissions.",
            "Under Basic Information, create an app-level token with connections:write and copy it (xapp-…).",
            "Paste both here.",
        ],
    },
}


def slack_manifest_url() -> str:
    manifest = Path(__file__).resolve().parents[1] / "channels" / "slack_manifest.yml"
    if not manifest.exists():
        return "https://api.slack.com/apps?new_app=1"
    return "https://api.slack.com/apps?new_app=1&manifest_yaml=" + quote(manifest.read_text())


def make_router(get_agent: Callable[[], Any], apply: Callable[[], dict[str, bool]]) -> APIRouter:
    router = APIRouter(
        prefix="/app/connections",
        tags=["Connections"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def view() -> dict[str, Any]:
        settings = get_agent().settings
        with session_scope() as s:
            result = store.status(s, settings)
        result.pop("anthropic", None)  # the AI model is connected on /app/providers, not here
        _, where = master_key()
        guides = {k: dict(v) for k, v in GUIDES.items()}
        guides["slack"]["url"] = slack_manifest_url()
        guides["google"]["redirect_uri"] = google_redirect_uri()
        guides["google"]["signin_redirect_uri"] = (
            f"{get_settings().frontend_origin.rstrip('/')}/api/auth/google/callback"
        )
        return {"services": result, "guides": guides, "keys_kept_in": where}

    def connect(provider: str, checked: check.Checked, user_id: uuid.UUID) -> dict[str, Any]:
        with session_scope() as s:
            if provider == "telegram":  # each owner's own bot, serving their Ava only (ADR 0009 T4)
                bot = checked.values["bot_token"].split(":", 1)[0]
                try:
                    endpoints.register_telegram(s, bot)
                except endpoints.EndpointTaken as exc:
                    raise HTTPException(
                        409, "This bot is already connected to another Ava. Create a new bot with @BotFather."
                    ) from exc
            if provider == "slack":  # each owner's own Slack app, serving their Ava only
                try:
                    endpoints.register_slack(s, checked.values.get("team_id", ""), checked.values["app_token"])
                except endpoints.EndpointTaken as exc:
                    raise HTTPException(
                        409, "This Slack app is already connected to another Ava. Create a new Slack app for yours."
                    ) from exc
            store.save(s, provider, checked.values, user_id=user_id, meta=checked.meta)
            if provider in health.PROVIDERS:
                health.reset(s, provider)  # the service just accepted this token
        telegram_username.cache_clear()
        apply()
        return view()

    def checked_or_400(fn: Callable[[], check.Checked]) -> check.Checked:
        try:
            return fn()
        except check.CheckFailed as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get(
        "",
        summary="Connection status",
        description="Which services are connected, how, and a masked hint of each credential.",
    )
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        return view()

    @router.post(
        "/telegram",
        summary="Connect Telegram",
        description="Checks the bot token with Telegram, stores it encrypted and starts the listener.",
    )
    def connect_telegram(
        body: TelegramBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        return connect("telegram", checked_or_400(lambda: check.check_telegram(body.bot_token, http_client())), user_id)

    @router.post(
        "/slack",
        summary="Connect Slack",
        description="Checks the bot and app-level tokens with Slack, stores them encrypted and starts Socket Mode.",
    )
    def connect_slack(
        body: SlackBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        return connect(
            "slack", checked_or_400(lambda: check.check_slack(body.bot_token, body.app_token, http_client())), user_id
        )

    @router.post(
        "/deepgram",
        summary="Connect Deepgram (voice call)",
        description="Checks the API key with Deepgram and stores it encrypted. Used for speech-to-text on the call.",
    )
    def connect_deepgram(
        body: VoiceKeyBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        return connect("deepgram", checked_or_400(lambda: check.check_deepgram(body.api_key, http_client())), user_id)

    @router.post(
        "/elevenlabs",
        summary="Connect ElevenLabs (voice call)",
        description="Checks the API key with ElevenLabs and stores it encrypted. Used for Ava's voice on the call.",
    )
    def connect_elevenlabs(
        body: VoiceKeyBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        return connect(
            "elevenlabs", checked_or_400(lambda: check.check_elevenlabs(body.api_key, http_client())), user_id
        )

    @router.post(
        "/google",
        summary="Save your Google OAuth client",
        description=(
            "The client ID and secret of the OAuth client you made in Google Cloud (Desktop or Web app). "
            "Stored encrypted. Then use Connect to sign in."
        ),
    )
    def google_client(
        body: GoogleClientBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        client_id = body.client_id.strip()
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise HTTPException(
                400, "That doesn't look like a Google client ID (it ends in .apps.googleusercontent.com)."
            )
        with session_scope() as s:
            # A sign-in made with an earlier client doesn't work with this one: sign in again.
            store.remove_field(s, "google", "refresh_token")
            store.save(
                s, "google", {"client_id": client_id, "client_secret": body.client_secret.strip()},
                user_id=user_id, meta={"redirect_uri": google_redirect_uri()},
            )  # fmt: skip
        return view()

    @router.post(
        "/google/start",
        summary="Start Google sign-in",
        description="Returns the Google sign-in URL (OAuth 2.1 with PKCE). Open it in the browser.",
    )
    def google_start(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, str]:
        from superaide.skills import calendar

        with session_scope() as s:
            events = calendar.actions_on(s)  # reconnecting keeps the right to change events, if it was given
        return {"url": begin_google(user_id, events=events)}

    @router.get(
        "/google/callback",
        summary="Google sign-in callback",
        description=(
            "Google sends the browser here. Checks the one-time state from Start (which is tied to the signed-in "
            "owner), stores the refresh token, then sends the browser back to Connections with ?google=<result>."
        ),
        include_in_schema=False,
    )
    def google_callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        # No session check here: the one-time state, made by the owner's own /start, is the proof. That way a
        # lapsed session still lands back on Connections with a message instead of a bare 401.
        pending: dict[str, Any] = {}
        with session_scope() as s:
            row = s.get(PlatformState, _pending_key(state)) if state else None
            if row is not None:
                pending = dict(row.value or {})
                s.delete(row)  # one use only; a stray request can't wipe a real sign-in
        page = BACK_PAGES.get(str(pending.get("back") or ""), BACK_PAGES["connections"])
        back = f"{get_settings().frontend_origin.rstrip('/')}{page}"
        matches = bool(pending)
        fresh = (
            bool(pending.get("at")) and datetime.now(UTC) - datetime.fromisoformat(pending["at"]) < google.PENDING_TTL
        )
        if error or not code:
            return RedirectResponse(f"{back}?google=cancelled", status_code=303)
        if not matches or not fresh or not pending.get("user_id") or not pending.get("agent_id"):
            return RedirectResponse(f"{back}?google=expired", status_code=303)
        owner_scope = InstanceScope(tenant_id=uuid.UUID(pending["tenant_id"]), agent_id=uuid.UUID(pending["agent_id"]))
        with use_instance(owner_scope):  # the owner who started the sign-in, whoever's browser comes back
            if pending.get("purpose") == "gmail":  # the Gmail capability's sign-in (app/gmail.py)
                from superaide.app.gmail import finish

                return finish(code, pending)
            return _finish_google(code, pending, back)

    def _finish_google(code: str, pending: dict[str, Any], back: str) -> RedirectResponse:
        settings = runtime_settings()
        try:
            tokens = google.exchange_code(
                client_id=settings.google_client_id, client_secret=settings.google_client_secret, code=code,
                verifier=str(pending["verifier"]), redirect_uri=google_redirect_uri(), client=http_client(),
                scopes=list(pending.get("scopes") or google.SCOPES),
            )  # fmt: skip
        except google.ScopeNotGranted:
            wanted_events = google.EVENTS_SCOPE in (pending.get("scopes") or [])
            outcome = "no_actions" if wanted_events else "no_calendar"
            return RedirectResponse(f"{back}?google={outcome}", status_code=303)
        except google.ApiDisabled:
            return RedirectResponse(f"{back}?google=api_disabled", status_code=303)
        except google.GoogleError as exc:
            log.warning("google sign-in failed: %s", exc)  # status and Google's reason only
            return RedirectResponse(f"{back}?google=failed", status_code=303)
        with session_scope() as s:
            store.save(
                s, "google", {"refresh_token": str(tokens["refresh_token"])}, user_id=uuid.UUID(pending["user_id"]),
                meta={"scopes": sorted(set(str(tokens.get("scope", "")).split()))},
            )  # fmt: skip
        return RedirectResponse(f"{back}?google=connected", status_code=303)

    @router.post(
        "/{provider}/disconnect",
        summary="Disconnect a service",
        description=(
            "Deletes the stored credential and stops its listener. "
            "`provider` is `telegram`, `slack`, `google`, `deepgram` or `elevenlabs`."
        ),
    )
    def disconnect(
        provider: str, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        if provider not in {"telegram", "slack", "google", "deepgram", "elevenlabs"}:
            raise HTTPException(404, "Unknown connection")
        with session_scope() as s:
            store.remove(s, provider, user_id=user_id)
            if provider in ("telegram", "slack"):
                endpoints.remove(s, provider)
            if provider in health.PROVIDERS:
                health.reset(s, provider)
        telegram_username.cache_clear()
        apply()
        return view()

    return router
