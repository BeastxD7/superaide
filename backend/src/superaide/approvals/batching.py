"""Batch approval (#408): several safe, alike requests as one card with a preview, one decision.

Code decides what may be grouped, never the model. A request joins a batch only when ALL of these hold:
* its kind is on the short allow-list BATCHABLE (own-calendar writes) and not on NEVER_BATCH;
* it is waiting, unexpired, sealed, and its payload still matches the seal (`binding.check`);
* nobody else is told (no guests, no notify), nothing heard on a call, no warning chip, and the turn that asked
  was recorded as untainted (`payload.tainted is False`; a row that does not say is left out).
A batch needs at least MIN_GROUP such requests. The owner's decision names the exact set (`ids`) and a digest of
every item's seal; if any item was added, removed or changed since the card was shown, the digest differs and
NOTHING is decided. Each item is then resolved one by one through `policy.actions.resolve`, so the per-item
binding check runs again just before each action. While the rubber-stamp brake is engaged a batch cannot be
approved at all (a "no" always can be). A batch is never offered for sending, money, deleting or access.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.approvals.binding import check, waiting
from superaide.approvals.models import Approval
from superaide.platform.audit import audit

# Own-calendar writes only. Anything not listed is never batched.
BATCHABLE: dict[str, str] = {
    "calendar_create_event": "Add events to your calendar",
    "calendar_move_event": "Move events in your calendar",
}
# The hard list: these can never be batched, whatever BATCHABLE says (a test pins that they are disjoint).
NEVER_BATCH = frozenset(
    {
        "gmail_send",
        "ask_reply",
        "reply_on_behalf",
        "message_someone_else",
        "publish_post",
        "grant_access",
        "delete_memory",
        "calendar_decline_event",
        "identity_claim_pick",
        "create_account",
        "create_profile",
        "open_pull_request",
        "post_review",
    }
)
MIN_GROUP = 2
MAX_GROUP = 25  # the hard ceiling; APPROVAL_BATCH_MAX may only lower it (0 = off)


def enabled() -> bool:
    from superaide.platform.config import get_settings

    return get_settings().approval_batch_max >= MIN_GROUP


def limit() -> int:
    from superaide.platform.config import get_settings

    return min(MAX_GROUP, get_settings().approval_batch_max)


class BatchError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Group:
    kind: str
    label: str
    items: list[Approval]

    @property
    def digest(self) -> str:
        return digest_of(self.items)


def digest_of(items: list[Approval]) -> str:
    """One hash over every item's id and sealed content: a change to any item, or to who is in the batch, changes it."""
    lines = sorted(f"{a.id}:{a.bound_hash or ''}" for a in items)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def eligible(a: Approval) -> bool:
    from superaide.policy import kinds

    p = a.payload or {}
    event: dict[str, Any] = p["event"] if isinstance(p.get("event"), dict) else {}
    patch: dict[str, Any] = p["patch"] if isinstance(p.get("patch"), dict) else {}
    return (
        a.kind in BATCHABLE
        and a.kind not in NEVER_BATCH
        and a.status == "pending"
        and kinds.has_executor(a.kind)
        and check(a) == "ok"
        and p.get("tainted") is False
        and not p.get("notify")
        and not p.get("origin")
        and not p.get("warnings")
        and not event.get("attendees")
        and not patch.get("attendees")
    )


def groups(session: Session) -> list[Group]:
    """Waiting requests that may be grouped, one group per kind, at least MIN_GROUP long (oldest first, capped)."""
    if not enabled():
        return []
    cap = limit()
    rows = session.scalars(select(Approval).where(waiting()).order_by(Approval.created_at)).all()
    by_kind: dict[str, list[Approval]] = {}
    for a in rows:
        if eligible(a):
            by_kind.setdefault(a.kind, []).append(a)
    return [Group(k, BATCHABLE[k], v[:cap]) for k, v in by_kind.items() if len(v) >= MIN_GROUP]


def decide(
    session: Session,
    *,
    ids: list[uuid.UUID],
    digest: str,
    approve: bool,
    decided_by: str,
    resolve: Callable[..., Approval],
    approval_error: type[Exception],
    brake_engaged: Callable[[Session], bool],
) -> list[dict[str, Any]]:
    """Decide a whole batch or nothing. Raises BatchError (and decides nothing) when the set or content changed.
    The caller hands in `policy.actions.resolve`, its ApprovalError and the brake check: approvals must not import
    policy (policy imports approvals), so the dependency points the other way."""
    from superaide.platform.config import get_settings

    if len(set(ids)) != len(ids) or not MIN_GROUP <= len(ids) <= limit():
        raise BatchError(422, "A batch is two or more different requests.")
    rows = [session.get(Approval, i) for i in ids]
    if any(r is None for r in rows):
        raise BatchError(404, "No such approval")
    items: list[Approval] = [r for r in rows if r is not None]
    if len({a.kind for a in items}) != 1 or not all(eligible(a) for a in items) or digest_of(items) != digest:
        audit(session, "policy", "approval_batch_refused", approval_ids=[str(i) for i in ids])
        session.commit()  # keep the audit line
        raise BatchError(409, "These requests changed since you looked. Nothing was decided; look again.")
    if approve and get_settings().rubber_stamp_brake_enabled and brake_engaged(session):
        raise BatchError(422, "You have been approving fast. Open each card and give a reason, or say no to the batch.")
    audit(
        session,
        "policy",
        "approval_batch_decided",
        kind=items[0].kind,
        verdict="approve" if approve else "reject",
        approval_ids=[str(a.id) for a in items],
    )
    out: list[dict[str, Any]] = []
    for a in items:
        try:
            resolve(session, a, approve=approve, decided_by=decided_by, via="app")
        except approval_error as exc:  # one item already decided elsewhere: report it, carry on with the rest
            out.append({"id": str(a.id), "status": a.status, "result": str(exc)})
            continue
        out.append({"id": str(a.id), "status": a.status, "result": (a.payload or {}).get("result")})
    return out
