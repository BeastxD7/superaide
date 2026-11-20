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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from superaide.channels import health
from superaide.policy.killswitch import get_state, set_state

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


# Why the model cannot be used for a configuration reason (#633). The provider layer classifies, the model never does.
KEY_MISSING, KEY_INVALID, CREDIT_LOW, SPEND_CAP = "key_missing", "key_invalid", "credit_low", "spend_cap"
CONTEXT_SMALL = "context_small"  # a local Ollama model's window is smaller than Ava's prompt (it would silently cut it)
CONTEXT_CUT = "context_cut"  # a server read fewer prompt tokens than were sent: it cut the start of the prompt (#641)
REASONS = (KEY_MISSING, KEY_INVALID, CREDIT_LOW, SPEND_CAP, CONTEXT_SMALL, CONTEXT_CUT)
TROUBLE_KEY = "ai_trouble"
TROUBLE_FRESH = timedelta(minutes=60)  # credit and cap trouble is forgotten unless seen again (credits get topped up)
MODELS_PAGE = "/dashboard/models"
OWNER_TEXT = {
    KEY_MISSING: "I have no AI key yet, so I can't think. Add one on the AI model page.",
    KEY_INVALID: KEY_REJECTED_CHAT,
    CREDIT_LOW: "My AI provider says the account is out of credit. Add credit at the provider, then try again.",
    SPEND_CAP: "I've reached the spending cap, so I'm paused. Raise the cap on the AI model page.",
    CONTEXT_SMALL: "My local model's memory window is too small for me, so I forget things. See the AI model page.",
    CONTEXT_CUT: "My model's server cut the start of what I was told, so I may forget my rules. See the AI model page.",
}
ACTIONS = {
    KEY_MISSING: "Add a key",
    KEY_INVALID: "Check the key",
    CREDIT_LOW: "Open AI model settings",
    SPEND_CAP: "Raise the cap",
    CONTEXT_SMALL: "See how to fix it",
    CONTEXT_CUT: "See how to fix it",
}


CAUSES = {
    KEY_MISSING: "No AI key is set, so Ava cannot think (replies are offline echoes).",
    KEY_INVALID: "The AI provider rejected the key.",
    CREDIT_LOW: "The AI provider says the account is out of credit.",
    SPEND_CAP: "The spend cap was reached, so model calls are refused.",
    CONTEXT_SMALL: "A local Ollama model's context window is smaller than Ava's prompt, so Ollama cuts it off.",
    CONTEXT_CUT: "The model's server reported far fewer prompt tokens than were sent: it cut the start of the prompt.",
}
FIXES = {
    KEY_MISSING: "Add a key on the AI model page.",
    KEY_INVALID: "Check or replace the key on the AI model page.",
    CREDIT_LOW: "Add credit at the provider, then try again.",
    SPEND_CAP: "Raise the limit on the AI model page (or MAX_SPEND_USD).",
    CONTEXT_SMALL: "Make a bigger-window copy of the model with a Modelfile (num_ctx 16384) and choose it.",
    CONTEXT_CUT: "Give the model a bigger window (Ollama: a Modelfile with num_ctx 16384) or choose a model with one.",
}


def is_credit_error(exc: BaseException) -> bool:
    """The provider says the account has no credit left (Anthropic 400 "credit balance is too low", HTTP 402)."""
    status = getattr(exc, "status_code", None)
    if status == 402:
        return True
    text = str(exc).lower()
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict):
            text += " " + str(inner.get("message", "")).lower() + " " + str(inner.get("code", "")).lower()
    return (status in (400, 429, None) or type(exc).__name__ == "BadRequestError") and any(
        s in text for s in ("credit balance is too low", "insufficient_quota", "insufficient credit", "out of credit")
    )


def classify(exc: BaseException) -> str | None:
    """key_invalid, credit_low or spend_cap for an error that needs the owner to act; None for a passing failure."""
    from superaide.brain.spend.budget import BudgetExceeded

    if isinstance(exc, BudgetExceeded):
        return SPEND_CAP
    if is_auth_error(exc):
        return KEY_INVALID
    if is_credit_error(exc):
        return CREDIT_LOW
    return None


def missing(settings: Any) -> bool:
    """A real model is wanted (FAKE_LLM off) but no provider has a key, so replies are offline echoes."""
    from superaide.brain.wiring import brain_status

    return not settings.fake_llm and brain_status(settings).reason == "no_provider"


def warn_if_missing(settings: Any) -> None:
    """At startup: say in the log when no AI key is set. Never raises, never prints a key."""
    try:
        if missing(settings):
            log.error(
                "No AI key is set, so replies are offline echoes. Add one on the AI model page "
                "(or set ANTHROPIC_API_KEY in backend/.env) and restart."
            )
    except Exception:
        log.exception("couldn't check the AI key at startup")


def record_trouble(reason: str, provider: str) -> None:
    """Best effort, in the instance in scope: the last configuration failure, for the banner. No message, no key."""
    from superaide.platform.db.session import session_scope

    try:
        with session_scope() as s:
            set_state(s, TROUBLE_KEY, {"reason": reason, "provider": provider, "at": datetime.now(UTC).isoformat()})
    except Exception:
        log.exception("couldn't record AI trouble")


def small_context(session: Session, settings: Any, *, fresh: bool = False) -> Any:
    """The first Ollama model in use (chat, triage, deep) whose window is smaller than Ava's prompt, if any.
    Local servers only: a hosted deployment never contacts an owner-typed address from here."""
    from sqlalchemy import select

    from superaide.brain.models import LlmProvider, ModelRole
    from superaide.brain.providers import ollama_ctx
    from superaide.platform.tenancy.context import mine

    if getattr(settings, "deploy_mode", "self_hosted") == "hosted":
        return None
    roles = {r.role: r for r in session.scalars(select(ModelRole).where(mine(ModelRole)))}
    for name in ("chat", "triage", "deep"):
        role = roles.get(name)
        row = (
            session.scalar(select(LlmProvider).where(mine(LlmProvider), LlmProvider.key == role.provider))
            if role
            else None
        )
        if role is None or row is None or row.preset != "ollama" or not row.enabled:
            continue
        found = ollama_ctx.warning(row.base_url, role.model, fresh=fresh)
        if found is not None:
            return found
    return None


def cut_notice(session: Session) -> dict[str, str] | None:
    """The server cut a prompt recently (brain/context_watch.py), seen in a reply's token count (#641)."""
    state = get_state(session, "prompt_truncated")  # written by brain/context_watch.py
    try:
        recent = bool(state) and datetime.now(UTC) - datetime.fromisoformat(state["seen"]) < timedelta(hours=24)
    except KeyError, ValueError:
        recent = False
    if not recent:
        return None
    detail = (
        f"{str(state.get('model', '')).split('::')[-1]} read {state.get('reported', 0):,} tokens of about "
        f"{state.get('sent_estimate', 0):,} sent (window {state.get('window', 0):,})."
    )
    return {
        "reason": CONTEXT_CUT,
        "message": f"{detail} {FIXES[CONTEXT_CUT]}",
        "action": ACTIONS[CONTEXT_CUT],
        "href": MODELS_PAGE,
        "fix": FIXES[CONTEXT_CUT],
    }


def notice(session: Session, settings: Any, *, fresh: bool = False) -> dict[str, str] | None:
    """What the owner is told in the app (banner and Check my setup): {"reason", "message", "action", "href"}.
    `fresh` asks a local Ollama again instead of using its answer from the last few minutes."""
    reason: str | None = None
    if missing(settings):
        reason = KEY_MISSING
    elif rejected(session):
        reason = KEY_INVALID
    else:
        cur = get_state(session, TROUBLE_KEY)
        try:
            fresh = datetime.now(UTC) - datetime.fromisoformat(cur["at"]) < TROUBLE_FRESH
        except KeyError, ValueError:
            fresh = False
        if fresh and cur.get("reason") in (CREDIT_LOW, SPEND_CAP):
            reason = cur["reason"]
    if reason is None:
        found = small_context(session, settings, fresh=fresh)
        if found is None:
            return cut_notice(session)
        return {
            "reason": CONTEXT_SMALL,
            "message": f"{found.message} {found.steps}",
            "action": ACTIONS[CONTEXT_SMALL],
            "href": MODELS_PAGE,
            "fix": found.steps,
        }
    return {"reason": reason, "message": OWNER_TEXT[reason], "action": ACTIONS[reason], "href": MODELS_PAGE}


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
    set_state(session, TROUBLE_KEY, {})


def message_for(provider: str) -> str:
    return CREDITS_UNAVAILABLE_CHAT if provider == "credits" else KEY_REJECTED_CHAT


def status_view(session: Session) -> dict[str, Any]:
    bad = rejected(session)
    return {"rejected": bad, "message": SHORT if bad else ""}
