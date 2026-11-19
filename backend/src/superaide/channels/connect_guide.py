"""The guided connect for Slack and Telegram (#410): a manifest made for the owner's chosen name, and a check of
each token as it is pasted, before anything is saved.

Nothing here stores or logs a token, and no answer carries one back: only the service's verdict, the names of
missing permissions and the bot or workspace name.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import yaml

from superaide.channels.slack import scopes
from superaide.identity.vault import check

MANIFEST_FILE = Path(__file__).resolve().parent / "slack" / "manifest.yml"
CREATE_URL = "https://api.slack.com/apps?new_app=1"
NAME_MAX = 35  # Slack's limit for an app name
DEFAULT_NAME = "Ava"

# Missing one of these breaks the part of Ava that needs it, but she still replies. Everything else in
# scopes.REQUIRED_SCOPES is needed to hear and answer at all.
OPTIONAL_SCOPES = ("channels:history", "groups:history", "files:read")


def clean_name(name: str | None) -> str:
    """A name Slack accepts: letters, digits, spaces and . _ - ', no longer than Slack allows."""
    spaced = re.sub(r"\s+", " ", name or "")
    kept = re.sub(r"[^\w .'-]", "", spaced, flags=re.UNICODE).strip()[:NAME_MAX].strip()
    return kept or DEFAULT_NAME


def render_manifest(name: str | None = None) -> str:
    """The Slack app manifest (YAML) with the owner's chosen name, from the one checked-in file."""
    data = yaml.safe_load(MANIFEST_FILE.read_text())
    chosen = clean_name(name)
    data["display_information"]["name"] = chosen
    data["features"]["bot_user"]["display_name"] = chosen
    header = (
        "# Made by Superaide. Paste at https://api.slack.com/apps -> Create New App -> From a manifest.\n"
        "# Then Install App for the Bot User OAuth Token (xoxb-), and make an app-level token\n"
        "# with connections:write under Basic Information (xapp-).\n"
    )
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def create_url(name: str | None = None) -> str:
    """Slack's 'create app' page with the manifest filled in."""
    return f"{CREATE_URL}&manifest_yaml={quote(render_manifest(name))}"


def split_missing(missing: list[str]) -> tuple[list[str], list[str]]:
    """(needed to hear and answer, optional features) among the missing scopes."""
    required = [m for m in missing if m not in OPTIONAL_SCOPES and m not in scopes.AMBIENT_SCOPES]
    optional = [m for m in missing if m in OPTIONAL_SCOPES or m in scopes.AMBIENT_SCOPES]
    return required, optional


def _fail(error: str, **more: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, **more}


def check_telegram_token(token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    try:
        got = check.check_telegram(token, client)
    except check.CheckFailed as exc:
        return _fail(str(exc))
    return {"ok": True, "username": got.meta.get("username"), "name": got.meta.get("name")}


def check_slack_bot_token(token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """The bot token (xoxb-): accepted by Slack, and which permissions the installed app lacks."""
    bot = token.strip()
    if not bot.startswith("xoxb-"):
        return _fail("The bot token should start with xoxb- (OAuth & Permissions page).")
    result = scopes.check_scopes(bot, client)
    if result.status == scopes.UNREACHABLE:
        return _fail("Couldn't reach Slack. Check your internet connection and try again.")
    if result.status == scopes.REJECTED:
        return _fail(check.slack_bot_token_error(result.error))
    required, optional = split_missing([*result.missing, *result.ambient_missing])
    if required:
        return _fail(scopes.missing_message(required), missing_required=required, missing_optional=optional)
    return {
        "ok": True,
        "missing_required": [],
        "missing_optional": optional,
        "scopes_known": result.status == scopes.OK,
    }


def check_slack_app_token(token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """The app-level token (xapp-): Slack must be willing to open a Socket Mode connection with it."""
    app = token.strip()
    if not app.startswith("xapp-"):
        return _fail("The app-level token should start with xapp- (Basic Information page).")
    http = client or httpx.Client(timeout=15)
    try:
        body = http.post(
            "https://slack.com/api/apps.connections.open", headers={"Authorization": f"Bearer {app}"}
        ).json()
    except httpx.HTTPError, ValueError:
        return _fail("Couldn't reach Slack. Check your internet connection and try again.")
    if not isinstance(body, dict) or not body.get("ok"):
        code = str(body.get("error") or "") if isinstance(body, dict) else ""
        return _fail(check.slack_app_token_error(code))
    return {"ok": True}
