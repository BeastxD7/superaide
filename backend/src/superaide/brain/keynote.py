"""Tell the owner in the app that the AI key was rejected (issue #150): one note per three hours.

Split from `keyhealth.py`, which jobs, knowledge and the providers use to read the verdict; sending a note needs
`proactive.notify`, and the verdict readers must not depend on it (ADR 0015).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from superaide.brain.keyhealth import CREDITS_NOTE, NOTE_EVERY, OWNER_NOTE
from superaide.policy.killswitch import get_state, set_state
from superaide.proactive.notify import notify_owner


def note_owner(session: Session, owner: Any, provider: str, *, what: str = "") -> bool:
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
    text = CREDITS_NOTE if provider == "credits" else OWNER_NOTE
    notify_owner(session, owner, text + (f" ({what})" if what else ""), {"kind": "ai_key_rejected"}, channel="app")
    return True
