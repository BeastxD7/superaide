"""Tell the owner in the app that the AI key was rejected (issue #150): one note per three hours.

Split from `keyhealth.py`, which jobs, knowledge and the providers use to read the verdict; sending a note needs
`proactive.notify`, and the verdict readers must not depend on it (ADR 0015).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from superaide.brain.keyhealth import CREDIT_LOW, CREDITS_NOTE, NOTE_EVERY, OWNER_NOTE
from superaide.policy.killswitch import get_state, set_state
from superaide.proactive.notify import notify_owner


def note_owner(session: Session, owner: Any, provider: str, *, what: str = "", reason: str = "") -> bool:
    """One in-app note per three hours, however many calls fail. Returns whether one was sent."""
    state = get_state(session, "ai_key_note")
    last = state.get("at")
    now = datetime.now(UTC)
    if last:
        try:
            if now - datetime.fromisoformat(last) < NOTE_EVERY:
                return False
        except ValueError:
            pass
    set_state(session, "ai_key_note", {"at": now.isoformat()})
    if reason == CREDIT_LOW:
        text = (
            "I couldn't use the AI model: the provider says the account is out of credit. "
            "Add credit there, then check the AI model page."
        )
    else:
        text = CREDITS_NOTE if provider == "credits" else OWNER_NOTE
    notify_owner(session, owner, text + (f" ({what})" if what else ""), {"kind": "ai_key_rejected"}, channel="app")
    return True


TRUNCATION_NOTE_EVERY = timedelta(days=7)


def note_truncation(session: Session, owner: Any, model: str, window: int) -> bool:
    """Tell the owner in the app, once per model per week, that the server cut the start of a prompt (#641)."""
    state = get_state(session, "prompt_truncated_note")
    now = datetime.now(UTC)
    try:
        if model in state and now - datetime.fromisoformat(state[model]) < TRUNCATION_NOTE_EVERY:
            return False
    except ValueError:
        pass
    if owner is None:
        return False
    set_state(session, "prompt_truncated_note", {**state, model: now.isoformat()})
    text = (
        f"The model {model.split('::')[-1]} has a {window:,}-token window and its server cut the start of what I "
        "was told, so I may have forgotten my rules or the conversation. Open the AI model page to fix it."
    )
    notify_owner(session, owner, text, {"kind": "prompt_truncated"}, channel="app")
    return True
