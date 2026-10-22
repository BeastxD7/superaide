"""Circuit breaker (issue #140): a burst limit on deletes and sends.

Counted per owner's Ava, in code at the point an approved action is carried out, so the model cannot raise
the limit and a long chat (or a compacted one) cannot talk its way past it. The limit is the Nth call in a
sliding window; the next one trips the existing kill switch (`policy/killswitch.py`) and is not carried out.
Off unless CIRCUIT_BREAKER_ENABLED.
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import get_settings
from superaide.policy.killswitch import get_state, set_frozen, set_state

STATE_KEY = "breaker"
# Action kinds that delete, archive or send. Anything else is not counted.
BURST_KINDS = frozenset(
    {"gmail_send", "message_someone_else", "delete_memory", "calendar_decline_event", "publish_post"}
)
HALT_REPLY = "Stopped. I am paused and will not act or reply until you resume me in the app."


def allow(session: Session, kind: str, *, now: float | None = None) -> bool:
    """Count one `kind` action. False means the burst limit was hit: Ava is now frozen and the action must
    not run. Always True when the breaker is off or the kind is not a delete or send."""
    cfg = get_settings()
    if not cfg.circuit_breaker_enabled or kind not in BURST_KINDS:
        return True
    t = time.time() if now is None else now
    recent = [x for x in get_state(session, STATE_KEY).get("at", []) if t - x < cfg.circuit_breaker_window_seconds]
    if len(recent) >= cfg.circuit_breaker_limit:
        set_state(session, STATE_KEY, {"at": recent})
        set_frozen(session, True, actor="circuit_breaker")
        audit(
            session, "policy", "breaker_tripped", kind=kind, limit=cfg.circuit_breaker_limit,
            window_seconds=cfg.circuit_breaker_window_seconds,
        )  # fmt: skip
        return False
    set_state(session, STATE_KEY, {"at": [*recent, t]})
    return True


def is_halt_word(text: str) -> bool:
    """The whole message is the halt word (case and surrounding punctuation ignored). Only meaningful when
    the breaker is on; the caller checks that the sender is the verified owner."""
    cfg = get_settings()
    return cfg.circuit_breaker_enabled and text.strip().strip(".!").strip().lower() == cfg.halt_word.strip().lower()


def halt(session: Session, *, via: str) -> str:
    set_frozen(session, True, actor="halt_word")
    audit(session, "policy", "halt_word", via=via)
    return HALT_REPLY
