"""Whether the model provider accepts our key right now (issue #150).

A key that is wrong or revoked never starts working by waiting, so a 401/403 from the provider is
permanent for the run: background jobs stop retrying, the AI model page says "Key rejected: reconnect",
and chat tells the owner plainly. The verdict is stored per agent instance with the same mechanism as
channel health (channels/health.py, one `agent_state` key per provider, so it is per instance by
construction) and is cleared when a key is saved. Only the fact is stored, never the provider's message
(which could echo part of a key) and never the key.

Provider names are the adapter's ("anthropic", an OpenAI-compatible provider's id) or "credits" for calls
Superaide AI credits pay for.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from superaide.channels import health
from superaide.policy.killswitch import set_state

log = logging.getLogger(__name__)

PREFIX = "ai:"
NOTE_EVERY = timedelta(hours=3)
AUTH_NAMES = {"AuthenticationError", "PermissionDeniedError"}
KNOWN = ("anthropic", "credits")
SHORT = "Key rejected: reconnect"

KEY_REJECTED_CHAT = "My AI key was rejected: check it on the AI model page."
CREDITS_UNAVAILABLE_CHAT = "Superaide's credits are unavailable right now."
OWNER_NOTE = "I couldn't use the AI model: the key was rejected. Check it on the AI model page."
CREDITS_NOTE = "I couldn't use the AI model: Superaide's credits are unavailable right now."
OTHERS_CHAT = "Sorry, I can't answer right now."


def is_auth_error(exc: BaseException) -> bool:
    """A provider refusing our credentials (HTTP 401/403, authentication_error, invalid x-api-key)."""
    if getattr(exc, "key_rejected", False):
        return True
    if getattr(exc, "status_code", None) in (401, 403) or type(exc).__name__ in AUTH_NAMES:
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict) and inner.get("type") in {"authentication_error", "permission_error"}:
            return True
    return "invalid x-api-key" in str(exc).lower()


def mark(exc: BaseException, provider: str) -> None:
    exc.key_rejected = True  # type: ignore[attr-defined]
    exc.key_provider = provider  # type: ignore[attr-defined]


def provider_of(exc: BaseException) -> str:
    return str(getattr(exc, "key_provider", "") or "anthropic")


def _name(provider: str) -> str:
    return PREFIX + provider


def record_rejected(provider: str) -> None:
    """Best effort, in the instance in scope, in its own short transaction."""
    health.record(_name(provider), health.AUTH_FAILED, "key rejected")


def rejected(session: Session) -> list[str]:
    """The providers whose key was rejected, in this instance."""
    out = []
    for provider in KNOWN:
        if health.rejected(session, _name(provider)):
            out.append(provider)
    for provider in _extra(session):
        if provider not in out and health.rejected(session, _name(provider)):
            out.append(provider)
    return out


def _extra(session: Session) -> list[str]:
    from sqlalchemy import select

    from superaide.brain.models import LlmProvider

    try:
        return [str(p) for p in session.scalars(select(LlmProvider.key))]
    except Exception:
        return []


def any_rejected() -> bool:
    from superaide.platform.db.session import session_scope

    try:
        with session_scope() as s:
            return bool(rejected(s))
    except Exception:
        return False


def reset(session: Session) -> None:
    """A key was saved or changed: forget old verdicts (and allow a fresh note)."""
    for provider in {*KNOWN, *_extra(session)}:
        health.set_health(session, _name(provider), health.OK)
    set_state(session, "ai_key_note", {})


def message_for(provider: str) -> str:
    return CREDITS_UNAVAILABLE_CHAT if provider == "credits" else KEY_REJECTED_CHAT


def status_view(session: Session) -> dict[str, Any]:
    bad = rejected(session)
    return {"rejected": bad, "message": SHORT if bad else ""}
