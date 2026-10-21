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


def check_telegram(bot_token: str, client: httpx.Client | None = None) -> Checked:
    token = bot_token.strip()
    if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{30,50}", token):
        raise CheckFailed("That doesn't look like a bot token. BotFather sends one like 123456789:AAE…")
    try:
        r = _client(client).get(f"https://api.telegram.org/bot{token}/getMe")
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CheckFailed("Couldn't reach Telegram. Check your internet connection and try again.") from exc
    if not data.get("ok"):
        raise CheckFailed("Telegram didn't accept that token. Copy it again from @BotFather.")
    bot = data.get("result", {})
    return Checked(values={"bot_token": token}, meta={"username": bot.get("username"), "name": bot.get("first_name")})


def check_slack(bot_token: str, app_token: str, client: httpx.Client | None = None) -> Checked:
    bot, app = bot_token.strip(), app_token.strip()
    if not bot.startswith("xoxb-"):
        raise CheckFailed("The bot token should start with xoxb- (OAuth & Permissions page).")
    if not app.startswith("xapp-"):
        raise CheckFailed("The app-level token should start with xapp- (Basic Information page).")
    http = _client(client)
    try:
        auth = http.post("https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {bot}"}).json()
        conn = http.post(
            "https://slack.com/api/apps.connections.open", headers={"Authorization": f"Bearer {app}"}
        ).json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CheckFailed("Couldn't reach Slack. Check your internet connection and try again.") from exc
    if not auth.get("ok"):
        raise CheckFailed("Slack didn't accept the bot token. Install the app to your workspace and copy it again.")
    if not conn.get("ok"):
        raise CheckFailed(
            "Slack didn't accept the app-level token. Make sure Socket Mode is on and it has connections:write."
        )
    return Checked(
        values={"bot_token": bot, "app_token": app, "team_id": str(auth.get("team_id", ""))},
        meta={"team": auth.get("team"), "bot_user": auth.get("user")},
    )
