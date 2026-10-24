"""The Slack permissions (bot scopes) Ava needs, checked against what the installed app was granted (#447).

`auth.test` answers with an `x-oauth-scopes` header listing the scopes of the token. A missing scope is
logged once at startup (and on connect) as an ERROR, and recorded so the Connections page and the owner's
banner can name it. It never stops the app: only the part that needs the scope degrades (for example,
without `channels:history` Ava answers in a thread without reading it).

The same list is in `manifest.yml`; `tests/channels/test_slack_scopes.py` keeps the two equal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

# scope -> what stops working without it
REQUIRED_SCOPES: dict[str, str] = {
    "app_mentions:read": "hearing an @-mention in a channel",
    "chat:write": "replying",
    "im:history": "reading direct messages",
    "im:read": "finding direct-message channels",
    "im:write": "starting a direct message (links, reminders)",
    "users:read": "knowing who is writing",
    "channels:history": "reading a thread in a public channel",
    "groups:history": "reading a thread in a private channel",
}

OK, MISSING, REJECTED, UNREACHABLE, UNKNOWN = "ok", "missing", "rejected", "unreachable", "unknown"
AUTH_TEST_URL = "https://slack.com/api/auth.test"


@dataclass
class ScopeCheck:
    status: str
    granted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    error: str = ""


def check_scopes(bot_token: str, client: httpx.Client | None = None) -> ScopeCheck:
    """Ask Slack which scopes the bot token has. Never raises."""
    if not bot_token:
        return ScopeCheck(UNKNOWN)
    try:
        r = (client or httpx.Client(timeout=15)).post(AUTH_TEST_URL, headers={"Authorization": f"Bearer {bot_token}"})
        body = r.json()
    except httpx.HTTPError, ValueError:
        return ScopeCheck(UNREACHABLE, error="Slack couldn't be reached")
    if not isinstance(body, dict) or not body.get("ok"):
        return ScopeCheck(REJECTED, error=str(body.get("error") or "invalid_auth") if isinstance(body, dict) else "")
    header = r.headers.get("x-oauth-scopes")
    if header is None:
        return ScopeCheck(UNKNOWN)  # Slack didn't say: don't guess a problem
    granted = sorted({x.strip() for x in header.split(",") if x.strip()})
    missing = [s for s in REQUIRED_SCOPES if s not in granted]
    return ScopeCheck(MISSING if missing else OK, granted, missing)


def missing_message(missing: list[str]) -> str:
    return (
        "The Slack app is missing permissions: "
        + ", ".join(missing)
        + ". Add them under OAuth & Permissions > Bot Token Scopes, then reinstall the app to the workspace"
        + " and paste the new Bot User OAuth Token."
    )


def validate_and_record(bot_token: str, client: httpx.Client | None = None) -> ScopeCheck:
    """Run the check for a configured token, log the verdict, and record it for the app. No token: no check."""
    if not bot_token:
        return ScopeCheck(UNKNOWN)
    from superaide.channels import health

    result = check_scopes(bot_token, client)
    if result.status == MISSING:
        log.error("slack: %s", missing_message(result.missing))
    elif result.status == REJECTED:
        log.error("slack: the bot token was rejected (%s). Reconnect Slack under Connections.", result.error)
        health.record("slack", health.AUTH_FAILED, f"Slack rejected the token ({result.error})")
    elif result.status == UNREACHABLE:
        log.warning("slack: couldn't check the app's permissions (Slack unreachable)")
    if result.status in (OK, MISSING):
        health.record_missing_scopes("slack", result.missing)
    return result
