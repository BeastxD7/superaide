"""Checking a credential with its service before saving it.

Each check is one cheap read-only call. The error messages are written for a
non-technical owner and never echo the credential back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx


class CheckFailed(Exception):
    """A plain-language reason the credential didn't work."""


@dataclass
class Checked:
    values: dict[str, str]
    meta: dict[str, Any] = field(default_factory=dict)


def _client(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(timeout=15)


def check_anthropic(api_key: str, client: httpx.Client | None = None) -> Checked:
    key = api_key.strip()
    if not key.startswith("sk-ant-"):
        raise CheckFailed("That doesn't look like a Claude API key. Keys start with sk-ant-.")
    try:
        r = _client(client).get(
            "https://api.anthropic.com/v1/models",
            params={"limit": 1},
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    except httpx.HTTPError as exc:
        raise CheckFailed("Couldn't reach Anthropic. Check your internet connection and try again.") from exc
    if r.status_code in (401, 403):
        raise CheckFailed("Anthropic didn't accept that key. Copy it again from the Claude Console.")
    if r.status_code >= 400:
        raise CheckFailed(f"Anthropic answered with an error ({r.status_code}). Try again in a minute.")
    return Checked(values={"api_key": key})


def _object(r: httpx.Response) -> dict[str, Any]:
    """The JSON body, which must be an object; anything else counts as an unreadable answer."""
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def check_telegram(bot_token: str, client: httpx.Client | None = None) -> Checked:
    token = bot_token.strip()
    if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{30,50}", token):
        raise CheckFailed("That doesn't look like a bot token. BotFather sends one like 123456789:AAE…")
    try:
        data = _object(_client(client).get(f"https://api.telegram.org/bot{token}/getMe"))
    except (httpx.HTTPError, ValueError) as exc:
        raise CheckFailed("Couldn't reach Telegram. Check your internet connection and try again.") from exc
    if not data.get("ok"):
        raise CheckFailed("Telegram didn't accept that token. Copy it again from @BotFather.")
    bot = data.get("result")
    if not isinstance(bot, dict):
        raise CheckFailed("Telegram answered without the bot's details. Try again in a minute.")
    return Checked(values={"bot_token": token}, meta={"username": bot.get("username"), "name": bot.get("first_name")})


def slack_bot_token_error(code: str) -> str:
    """Why Slack refused the bot token, naming the page or setting to fix. `code` is Slack's own error word
    (never the token), included so a rare answer can still be looked up."""
    base = "Slack didn't accept the bot token."
    if code in ("token_revoked", "token_expired"):
        return f"{base} It was revoked: reinstall the app (Install App) and copy the new Bot User OAuth Token."
    if code == "account_inactive":
        return f"{base} The app or its workspace is no longer active: reinstall the app to your workspace."
    if code == "invalid_auth" or not code:
        return f"{base} Install the app to your workspace (Install App) and copy the Bot User OAuth Token again."
    return f"{base} Install the app to your workspace and copy it again (Slack said: {code})."


def slack_app_token_error(code: str) -> str:
    """Why Slack refused the app-level token, naming the exact scope or setting."""
    base = "Slack didn't accept the app-level token."
    if code == "missing_scope":
        return (
            f"{base} It needs the connections:write scope: under Basic Information > App-Level Tokens, "
            "make a new one with it."
        )
    if code == "not_allowed_token_type":
        return (
            f"{base} That is not an app-level token: make one under Basic Information > App-Level Tokens "
            "(it starts with xapp-)."
        )
    if code in ("invalid_auth", "not_authed", "token_revoked", "token_expired"):
        return (
            f"{base} Copy it again from Basic Information > App-Level Tokens, or make a new one with connections:write."
        )
    return f"{base} Make sure Socket Mode is on and it has connections:write (Slack said: {code or 'no reason'})."


def check_slack(bot_token: str, app_token: str, client: httpx.Client | None = None) -> Checked:
    bot, app = bot_token.strip(), app_token.strip()
    if not bot.startswith("xoxb-"):
        raise CheckFailed("The bot token should start with xoxb- (OAuth & Permissions page).")
    if not app.startswith("xapp-"):
        raise CheckFailed("The app-level token should start with xapp- (Basic Information page).")
    http = _client(client)
    try:
        auth = _object(http.post("https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {bot}"}))
        conn = _object(
            http.post("https://slack.com/api/apps.connections.open", headers={"Authorization": f"Bearer {app}"})
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise CheckFailed("Couldn't reach Slack. Check your internet connection and try again.") from exc
    if not auth.get("ok"):
        raise CheckFailed(slack_bot_token_error(str(auth.get("error") or "")))
    if not conn.get("ok"):
        raise CheckFailed(slack_app_token_error(str(conn.get("error") or "")))
    return Checked(
        values={"bot_token": bot, "app_token": app, "team_id": str(auth.get("team_id", ""))},
        meta={"team": auth.get("team"), "bot_user": auth.get("user")},
    )


def check_deepgram(api_key: str, client: httpx.Client | None = None) -> Checked:
    """Deepgram, for speech-to-text (and its Aura voices) on the voice call."""
    key = api_key.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{20,200}", key):
        raise CheckFailed("That doesn't look like a Deepgram API key. Copy it again from the Deepgram console.")
    try:
        r = _client(client).get("https://api.deepgram.com/v1/projects", headers={"Authorization": f"Token {key}"})
    except httpx.HTTPError as exc:
        raise CheckFailed("Couldn't reach Deepgram. Check your internet connection and try again.") from exc
    if r.status_code in (401, 403):
        raise CheckFailed("Deepgram didn't accept that key. Copy it again from the Deepgram console.")
    if r.status_code >= 400:
        raise CheckFailed(f"Deepgram answered with an error ({r.status_code}). Try again in a minute.")
    return Checked(values={"api_key": key})


def check_elevenlabs(api_key: str, client: httpx.Client | None = None) -> Checked:
    """ElevenLabs, for Ava's natural voice on the voice call."""
    key = api_key.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", key):
        raise CheckFailed("That doesn't look like an ElevenLabs API key. Copy it again from your ElevenLabs settings.")
    try:
        r = _client(client).get("https://api.elevenlabs.io/v1/models", headers={"xi-api-key": key})
    except httpx.HTTPError as exc:
        raise CheckFailed("Couldn't reach ElevenLabs. Check your internet connection and try again.") from exc
    if r.status_code in (401, 403):
        raise CheckFailed("ElevenLabs didn't accept that key. Check it has text-to-speech access.")
    if r.status_code >= 400:
        raise CheckFailed(f"ElevenLabs answered with an error ({r.status_code}). Try again in a minute.")
    return Checked(values={"api_key": key})
