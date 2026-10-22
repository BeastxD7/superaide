"""Rubber-Stamp Brake (issue #134): notice when the owner approves without reading, and tighten up.

The signal is the owner's own approvals: how fast each card was decided and whether any was ever
refused. The thresholds are numbers in this module, not prompt text, and the brake can only add
friction (a typed reason on high-risk cards, fewer cards shown at once). It never loosens anything, it
never touches a rejection, and nothing the model says reaches it. Off unless RUBBER_STAMP_BRAKE_ENABLED.

Latency is `decided_at - created_at` of an Approval, so no new table is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Approval

WINDOW = 20  # the most recent decided approvals that count
MIN_DECISIONS = 8  # fewer than this and there is nothing to judge
FAST_MEDIAN_S = 5.0  # a median under this is faster than reading a card
MAX_REJECT_RATE = 0.05  # at or below this, the owner is effectively always saying yes
MIN_REASON_CHARS = 10
PENDING_CAP = 3  # cards shown at once while the brake is on
# Cards whose approval does something that is hard to take back or goes to another person.
HIGH_RISK = frozenset(
    {
        "publish_post",
        "message_someone_else",
        "grant_access",
        "delete_memory",
        "gmail_send",
        "ask_reply",
        "reply_on_behalf",
        "calendar_create_event",
        "calendar_move_event",
        "calendar_decline_event",
    }
)


@dataclass(frozen=True)
class BrakeState:
    engaged: bool
    decisions: int
    median_latency_s: float | None
    reject_rate: float | None
    per_kind: dict[str, dict[str, float | int]]


def _stats(rows: list[Approval]) -> BrakeState:
    latencies = [(a.decided_at - a.created_at).total_seconds() for a in rows if a.decided_at and a.created_at]
    n = len(latencies)
    rejected = sum(1 for a in rows if a.status == "rejected")
    per_kind: dict[str, dict[str, float | int]] = {}
    for a in rows:
        if not (a.decided_at and a.created_at):
            continue
        k = per_kind.setdefault(a.kind, {"decisions": 0, "rejected": 0, "_total": 0.0})
        k["decisions"] += 1
        k["rejected"] += 1 if a.status == "rejected" else 0
        k["_total"] += (a.decided_at - a.created_at).total_seconds()
    for k in per_kind.values():
        k["mean_latency_s"] = round(float(k.pop("_total")) / int(k["decisions"]), 2)
    if n < MIN_DECISIONS:
        return BrakeState(False, n, round(median(latencies), 2) if latencies else None, None, per_kind)
    med = median(latencies)
    rate = rejected / len(rows)
    return BrakeState(med < FAST_MEDIAN_S and rate <= MAX_REJECT_RATE, n, round(med, 2), round(rate, 3), per_kind)


def state(session: Session) -> BrakeState:
    rows = list(
        session.scalars(
            select(Approval)
            .where(Approval.status.in_(("approved", "rejected")), Approval.decided_at.is_not(None))
            .order_by(Approval.decided_at.desc())
            .limit(WINDOW)
        )
    )
    return _stats(rows)


def needs_reason(kind: str, brake: BrakeState) -> bool:
    return brake.engaged and kind in HIGH_RISK


def check(session: Session, approval: Approval, *, approve: bool, reason: str | None) -> str | None:
    """None to carry on, or the sentence to show the owner. Only an approval can be held back."""
    if not approve:
        return None
    brake = state(session)
    if not needs_reason(approval.kind, brake):
        return None
    if len((reason or "").strip()) >= MIN_REASON_CHARS:
        return None
    audit(session, "policy", "brake_held_approval", kind=approval.kind, approval_id=approval.id)
    return (
        "You have been approving very quickly. For this one, type a short reason (at least "
        f"{MIN_REASON_CHARS} characters) so we know you read it."
    )
