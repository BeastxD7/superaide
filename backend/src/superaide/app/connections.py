"""/app/connections: connect Claude, Telegram and Slack from the app.

Owner only. A credential is checked with its service, encrypted, saved, and
applied at once (the agent and listeners are rebuilt). Responses never contain
a credential, only a masked hint.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.routes import owner_person_id, telegram_username
from superaide.auth.routes import current_user_id, require_csrf_header
from superaide.db.session import session_scope
from superaide.vault import check, store
from superaide.vault.crypto import master_key


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=15)


class AnthropicBody(BaseModel):
    api_key: str = Field(min_length=10, max_length=300)


class TelegramBody(BaseModel):
    bot_token: str = Field(min_length=10, max_length=100)


class SlackBody(BaseModel):
    bot_token: str = Field(min_length=10, max_length=300)
    app_token: str = Field(min_length=10, max_length=300)


GUIDES = {
    "anthropic": {
        "url": "https://platform.claude.com/dashboard",
        "steps": [
            "Open the Claude Console and sign in.",
            "Create a workspace for Ava and set a monthly spend limit (for example US$10).",
            "In that workspace, create an API key and copy it.",
            "Paste it here. I check it, then keep it encrypted.",
        ],
    },
    "telegram": {
        "url": "https://t.me/BotFather",
        "steps": [
            "Open @BotFather in Telegram and send /newbot.",
            "Pick a name (e.g. Ava for Shashank) and a username ending in bot.",
            "BotFather replies with a token like 123456789:AAE…. Copy it.",
            "Paste it here. Then open your bot once so it knows it's you.",
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
    router = APIRouter(prefix="/app/connections", dependencies=[Depends(require_csrf_header)])

    def view() -> dict[str, Any]:
        settings = get_agent().settings
        with session_scope() as s:
            result = store.status(s, settings)
        _, where = master_key()
        guides = {k: dict(v) for k, v in GUIDES.items()}
        guides["slack"]["url"] = slack_manifest_url()
        return {"services": result, "guides": guides, "keys_kept_in": where}

    def connect(provider: str, checked: check.Checked, user_id: uuid.UUID) -> dict[str, Any]:
        with session_scope() as s:
            store.save(s, provider, checked.values, user_id=user_id, meta=checked.meta)
        telegram_username.cache_clear()
        apply()
        return view()

    def checked_or_400(fn: Callable[[], check.Checked]) -> check.Checked:
        try:
            return fn()
        except check.CheckFailed as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("")
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        return view()

    @router.post("/anthropic")
    def connect_anthropic(
        body: AnthropicBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        return connect("anthropic", checked_or_400(lambda: check.check_anthropic(body.api_key, http_client())), user_id)

    @router.post("/telegram")
    def connect_telegram(
        body: TelegramBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        return connect("telegram", checked_or_400(lambda: check.check_telegram(body.bot_token, http_client())), user_id)

    @router.post("/slack")
    def connect_slack(
        body: SlackBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        return connect(
            "slack", checked_or_400(lambda: check.check_slack(body.bot_token, body.app_token, http_client())), user_id
        )

    @router.post("/{provider}/disconnect")
    def disconnect(
        provider: str, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        if provider not in {"anthropic", "telegram", "slack"}:
            raise HTTPException(404, "Unknown connection")
        with session_scope() as s:
            store.remove(s, provider, user_id=user_id)
        telegram_username.cache_clear()
        apply()
        return view()

    return router
