"""Channel health: whether Telegram and Slack actually accept our credentials right now.

A saved bot token can stop working (revoked or regenerated in BotFather, Slack app uninstalled).
The Connections screen, Ava's prompt and reminder delivery all read this instead of assuming that
"a token is saved" means "messages get through".

Stored in `agent_state` (one key per provider), which is keyed by tenant and agent, so health is
already per agent instance (ADR 0009). Status is one of:

- ok: the last call worked (or nothing has failed yet)
- auth_failed: the service rejected the token (Telegram 401/404, Slack invalid_auth and friends)
- unreachable: the service couldn't be reached or answered with a server error

Recording is best-effort: a database hiccup must never break sending or polling.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from superaide.platform.audit import audit
from superaide.policy.killswitch import get_state, set_state

log = logging.getLogger(__name__)

OK = "ok"
AUTH_FAILED = "auth_failed"
UNREACHABLE = "unreachable"
PROVIDERS = ("telegram", "slack")
NAMES = {"telegram": "Telegram", "slack": "Slack"}


def key(provider: str) -> str:
    return f"channel_health:{provider}"


def get(session: Session, provider: str) -> dict[str, Any]:
    """{"status", "last_error", "since"}; a channel that never failed is ok."""
    value = get_state(session, key(provider))
    return {
        "status": value.get("status", OK),
        "last_error": value.get("last_error", ""),
        "since": value.get("since"),
        "missing_scopes": missing_scopes(session, provider),
    }


def scopes_key(provider: str) -> str:
    return f"channel_scopes:{provider}"


def missing_scopes(session: Session, provider: str) -> list[str]:
    """Permissions the service's app lacks (Slack bot scopes), from the last check at startup or connect."""
    return list(get_state(session, scopes_key(provider)).get("missing") or [])


def set_missing_scopes(session: Session, provider: str, missing: list[str]) -> bool:
    if missing_scopes(session, provider) == missing:
        return False
    set_state(session, scopes_key(provider), {"missing": missing})
    audit(session, "channels", "channel_scopes", provider=provider, missing=missing)
    return True


def record_missing_scopes(provider: str, missing: list[str]) -> None:
    from superaide.platform.db.session import session_scope

    try:
        with session_scope() as s:
            set_missing_scopes(s, provider, missing)
    except Exception:
        log.exception("couldn't record %s missing scopes", provider)


def rejected(session: Session, provider: str) -> bool:
    """True when the service refused our token: nothing can be sent there until it's reconnected."""
    return get(session, provider)["status"] == AUTH_FAILED


def set_health(session: Session, provider: str, status: str, error: str = "") -> bool:
    """Record a status in this session. Writes (and audits) only when something changed."""
    current = get_state(session, key(provider))
    if current.get("status", OK) == status and current.get("last_error", "") == error:
        return False
    since = current.get("since") if current.get("status", OK) == status else datetime.now(UTC).isoformat()
    set_state(session, key(provider), {"status": status, "last_error": error[:300], "since": since})
    if current.get("status", OK) != status:
        audit(session, "channels", "channel_health", provider=provider, status=status, error=error[:300])
    return True


def record(provider: str, status: str, error: str = "") -> None:
    """Record from code that has no session (messengers, the poller), in its own short transaction."""
    from superaide.platform.db.session import session_scope

    try:
        with session_scope() as s:
            set_health(s, provider, status, error)
    except Exception:  # never let bookkeeping break a send or the poller
        log.exception("couldn't record %s channel health", provider)


def reset(session: Session, provider: str) -> None:
    """A new token was saved, or the channel was disconnected: forget the old verdict."""
    set_health(session, provider, OK)
    set_missing_scopes(session, provider, [])


def describe(provider: str, health: dict[str, Any]) -> str:
    """Why nothing can be sent on this channel, in words the owner can act on."""
    name = NAMES.get(provider, provider)
    if health.get("status") == AUTH_FAILED:
        token = "bot token" if provider == "telegram" else "Slack token"
        return f"the {token} was rejected. Reconnect {name} under Connections."
    if health.get("status") == UNREACHABLE:
        return f"{name} couldn't be reached. This message is here in the app instead."
    return f"{name} didn't accept the message."


class DeliveryFailed(Exception):
    """A message couldn't be sent. `kind` is auth_failed, unreachable or refused. Never holds a token."""

    def __init__(self, provider: str, kind: str, detail: str) -> None:
        super().__init__(f"{provider} {kind}: {detail}")
        self.provider = provider
        self.kind = kind
        self.detail = detail
