"""Who decides whether the kNN head may act (#117): the owner, by pressing Promote. Never Ava.

Policy (the owner accepted these on #117):
- Manual. Ava only shows "eligible" with the numbers: z 1.96, at least 200 corrections inside a 30-day window, and a
  Wilson lower bound of 0.8 on how often the head agreed with the owner's own label (`knn.shadow_status`).
- Promotion is a record in `agent_state` (who, when, the evidence), audited. Only the owner's route writes it.
- Demotion: after the evidence has stayed under the gate for 14 days the promotion ends by itself and is audited. The
  owner can take it back at any time.
- What a promoted head may do (TRIAGE_KNN_RAISE_ENABLED, off): raise only. A message the model called calm and no rule
  silenced can be raised to the interrupt path; the stage cap, quiet hours and the interrupt budget still apply, and
  nothing is ever lowered or silenced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from superaide.platform.audit import audit
from superaide.policy.killswitch import get_state, set_state
from superaide.triage import knn

STATE_KEY = "triage_knn_promotion"
DEMOTE_AFTER_DAYS = 14
RAISE_AT = 0.8  # the head's score at or above which a calm message is raised
REVIEW_EVERY = timedelta(hours=1)


class PromotionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def state(session: Session) -> dict[str, Any]:
    return get_state(session, STATE_KEY)


def is_promoted(session: Session) -> bool:
    return bool(state(session).get("promoted"))


def raise_enabled() -> bool:
    from superaide.vault.store import runtime_settings

    s = runtime_settings()
    return bool(s.triage_enabled and s.triage_knn_enabled and s.triage_knn_raise_enabled)


def raise_active(session: Session) -> bool:
    """May the head raise a message right now? The flags, and the owner's promotion still standing."""
    return raise_enabled() and is_promoted(session)


def promote(session: Session, *, by: str, now: datetime | None = None) -> dict[str, Any]:
    """The owner's yes. Refused unless the evidence clears the gate right now; idempotent once promoted."""
    now = now or datetime.now(UTC)
    current = state(session)
    if current.get("promoted"):
        return current
    status = knn.shadow_status(session, enabled=True, now=now)
    if not status["eligible"]:
        raise PromotionError(409, "Not eligible yet: " + _why_not(status))
    record: dict[str, Any] = {
        "promoted": True,
        "promoted_at": now.isoformat(),
        "promoted_by": by[:100],
        "evidence": _evidence(status),
        "below_since": None,
        "reviewed_at": now.isoformat(),
    }
    set_state(session, STATE_KEY, record)
    audit(session, "triage", "triage_knn_promoted", by=by, **record["evidence"])
    return record


def demote(session: Session, *, reason: str, by: str, now: datetime | None = None) -> dict[str, Any]:
    """Take the promotion back (always allowed). The record keeps why and when."""
    now = now or datetime.now(UTC)
    current = state(session)
    if not current.get("promoted"):
        return current
    record = {
        "promoted": False,
        "demoted_at": now.isoformat(),
        "demoted_by": by[:100],
        "demoted_reason": reason[:200],
        "previous_promotion": {k: current.get(k) for k in ("promoted_at", "promoted_by", "evidence")},
    }
    set_state(session, STATE_KEY, record)
    audit(session, "triage", "triage_knn_demoted", by=by, reason=reason[:200])
    return record


def review(session: Session, *, now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """Demote after 14 days under the gate. Under the gate means no longer eligible (the bound fell, or the 30-day
    window holds fewer than 200 corrections). Coming back above it resets the clock. Cheap enough to call on every
    read: it does its work at most once an hour unless forced."""
    now = now or datetime.now(UTC)
    current = state(session)
    if not current.get("promoted"):
        return current
    last = current.get("reviewed_at")
    if not force and last and now - datetime.fromisoformat(last) < REVIEW_EVERY:
        return current
    status = knn.shadow_status(session, enabled=True, now=now)
    since = current.get("below_since")
    if status["eligible"]:
        since = None
    elif since is None:
        since = now.isoformat()
    elif now - datetime.fromisoformat(since) >= timedelta(days=DEMOTE_AFTER_DAYS):
        return demote(session, reason=f"under the gate for {DEMOTE_AFTER_DAYS} days", by="system", now=now)
    current = {**current, "below_since": since, "reviewed_at": now.isoformat()}
    set_state(session, STATE_KEY, current)
    return current


def _evidence(status: dict[str, Any]) -> dict[str, Any]:
    keys = ("n", "agree", "lower_bound", "z", "needs", "gate", "window_days")
    return {k: status[k] for k in keys}


def _why_not(status: dict[str, Any]) -> str:
    if status["n"] < status["needs"]:
        return f"{status['n']} of the {status['needs']} corrections needed in the last {status['window_days']} days."
    return f"the lower bound is {status['lower_bound']}, the gate is {status['gate']}."


def view(session: Session, status: dict[str, Any]) -> dict[str, Any]:
    """The owner's card: the shadow numbers plus where the promotion stands."""
    cur = state(session)
    promoted = bool(cur.get("promoted"))
    below = cur.get("below_since")
    days_left = None
    if promoted and below:
        left = timedelta(days=DEMOTE_AFTER_DAYS) - (datetime.now(UTC) - datetime.fromisoformat(below))
        days_left = max(0, left.days)
    return {
        **status,
        "promoted": promoted,
        "promoted_at": cur.get("promoted_at") if promoted else None,
        "can_promote": bool(status["eligible"] and not promoted),
        "can_demote": promoted,
        "below_gate_since": below if promoted else None,
        "demotes_in_days": days_left,
        "demote_after_days": DEMOTE_AFTER_DAYS,
        "raise_enabled": raise_enabled(),
        "acting": bool(promoted and raise_enabled()),
    }
