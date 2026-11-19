"""After each model call: did the server read what we sent? (#641)

Ollama and some other local servers cut the start of a prompt that is longer than the window and say so only in
their own log. The reply's `prompt_tokens` is the only trace, so it is compared with what was sent. A cut is
recorded in the audit trail (`prompt_truncated`), shown in Check my setup and on the AI model page, and the owner
is told once. Nothing here asks the model anything, and it never raises into a turn.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from superaide.brain import context_window as cw
from superaide.platform.audit import audit
from superaide.policy.killswitch import get_state

log = logging.getLogger(__name__)

STATE_KEY = "prompt_truncated"
AUDIT_EVERY = timedelta(hours=6)  # one audit row per model per this long, however many turns are cut
SHOWN_FOR = timedelta(hours=24)  # the warning stays until the model has read a whole prompt for a day


def window_for(session: Session, model_id: str, *, probe: bool = True) -> cw.WindowInfo:
    """The window of a model id as the loop names it: "<provider key>::<model>", or a bare Claude id."""
    from sqlalchemy import select

    from superaide.brain.models import LlmProvider
    from superaide.brain.providers.config import split
    from superaide.platform.tenancy.context import mine

    provider, name = split(model_id)
    if provider == "anthropic":
        return cw.resolve_window(name, provider="anthropic")
    row = session.scalar(select(LlmProvider).where(mine(LlmProvider), LlmProvider.key == provider))
    if row is None:
        return cw.resolve_window(name, provider=provider, probe=False)
    meta = row.meta or {}
    windows = meta.get("context_windows")  # the owner's override for one model, or for the whole provider
    override = (windows or {}).get(name) if isinstance(windows, dict) else None
    override = override or meta.get("context_window")
    return cw.resolve_window(
        name,
        provider=row.preset,
        base_url=row.base_url,
        override=override if isinstance(override, int) else None,
        probe=probe,
    )


def _save(session: Session, value: dict[str, Any]) -> None:
    """Upsert the state row without expiring the caller's objects: this runs in the middle of a turn."""
    from sqlalchemy.dialects.postgresql import insert

    from superaide.platform.db.models import AgentState
    from superaide.platform.tenancy.context import scope_or_default

    scope = scope_or_default()
    session.flush()
    stmt = insert(AgentState).values(tenant_id=scope.tenant_id, agent_id=scope.agent_id, key=STATE_KEY, value=value)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[AgentState.tenant_id, AgentState.agent_id, AgentState.key], set_={"value": value}
        )
    )


def current(session: Session) -> dict[str, Any] | None:
    """The last cut, while it is recent (for the banner and Check my setup)."""
    state = get_state(session, STATE_KEY)
    try:
        if state and datetime.now(UTC) - datetime.fromisoformat(state["seen"]) < SHOWN_FOR:
            return state
    except KeyError, ValueError:
        pass
    return None


def observe(
    session: Session,
    model_id: str,
    *,
    system: Any,
    messages: Any,
    tools: Any,
    usage: Any,
    max_tokens: int,
    owner_notice: bool = True,
) -> bool:
    """Compare the provider's prompt token count with the estimate of what was sent. True when it was cut."""
    try:
        reported = int(getattr(usage, "prompt_tokens", 0) or 0)
        sent = cw.estimate_request(system, messages, tools)
        if not cw.truncated(sent, reported):
            return False
        from superaide.platform.config import get_settings

        info = window_for(session, model_id, probe=getattr(get_settings(), "deploy_mode", "") != "hosted")
        now = datetime.now(UTC)
        before = get_state(session, STATE_KEY)
        first = before.get("at") if before.get("model") == model_id else None
        recent = False
        try:
            recent = bool(first) and now - datetime.fromisoformat(str(before.get("audited"))) < AUDIT_EVERY
        except ValueError:
            pass
        detail = {
            "model": model_id,
            "sent_estimate": sent,
            "reported": reported,
            "window": info.tokens,
            "window_source": info.source,
            "budget": cw.input_budget(info.tokens, max_tokens),
        }
        audited = before.get("audited") if recent else now.isoformat()
        stamps = {"at": first or now.isoformat(), "seen": now.isoformat(), "audited": audited}
        _save(session, {**detail, **stamps})
        if not recent:
            audit(session, "brain", "prompt_truncated", **detail)
            if owner_notice:
                from superaide.brain import keynote

                keynote.note_truncation(session, model_id, info.tokens)
        return True
    except Exception:
        log.exception("couldn't check whether the prompt was cut")
        return False
