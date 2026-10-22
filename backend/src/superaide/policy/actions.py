"""Which requested actions may run, need your approval, or are refused.

Authority comes from the person's relation in the database (set by you), never
from what a message claims ("the board approved this", "I'm the CEO"). At the
Infant stage nothing with side effects runs without your approval.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Approval
from superaide.identity.resolver import Sender
from superaide.identity.trust import Trust


@dataclass(frozen=True)
class Rule:
    min_trust: int
    manager_only: bool
    needs_approval: bool


RULES: dict[str, Rule] = {
    "publish_post": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "message_someone_else": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "grant_access": Rule(int(Trust.STEPPED_UP), manager_only=True, needs_approval=True),
    "delete_memory": Rule(int(Trust.STEPPED_UP), manager_only=True, needs_approval=True),
}
# Never through a chat channel, whatever the sender's trust: these need the signed-in app (or the
# operator's CLI). Anything not in RULES is refused too; this set names them so the refusal is explicit
# and tested. Approving is never an action at all: approvals are decided only by the app's routes.
NEVER_VIA_CHAT = {
    "change_identity",
    "change_trust",
    "approve_action",
    "change_connection",
    "change_credentials",
    "change_settings",
    "export_data",
    "delete_data",
}


@dataclass
class Decision:
    outcome: str  # approval | refused | none
    reason: str
    approval: Approval | None = None


def decide(session: Session, *, kind: str, summary: str, requester: Sender) -> Decision:
    if kind == "none":
        return Decision("none", "no action requested")
    if kind in NEVER_VIA_CHAT or kind not in RULES:
        audit(session, "policy", "action_refused", kind=kind, reason="not allowed via chat", requester=requester.name)
        return Decision("refused", "not allowed via chat")
    rule = RULES[kind]
    if rule.manager_only and requester.relation != "manager":
        audit(
            session,
            "policy",
            "action_refused",
            kind=kind,
            reason="requester is not the manager",
            requester=requester.name,
            relation=requester.relation,
            summary=summary[:300],
        )
        return Decision("refused", "only your manager can authorise this")
    if requester.trust < rule.min_trust:
        audit(
            session,
            "policy",
            "action_refused",
            kind=kind,
            reason="identity not verified strongly enough",
            requester=requester.name,
            trust=requester.trust,
            required_trust=rule.min_trust,
        )
        return Decision("refused", "this needs a stronger identity check; do it from the dashboard instead")
    approval = Approval(
        kind=kind,
        payload={"summary": summary, "trust_at_request": requester.trust, "required_trust": rule.min_trust},
        requested_by_person_id=requester.person.id if requester.person else None,
    )
    session.add(approval)
    session.flush()
    audit(session, "policy", "approval_requested", kind=kind, approval_id=approval.id)
    return Decision("approval", "waiting for your approval", approval)


class ApprovalError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# What each approved action actually does. Nothing is wired yet at the Infant stage:
# approving records your yes, and the action runs once a connector exists for it.
EXECUTORS: dict[str, Callable[[Session, Approval], str]] = {}


def _load_executors() -> None:
    """Capabilities that carry out approved actions (imported here, not at the top: they import policy)."""
    from superaide.skills import gmail

    EXECUTORS.setdefault("gmail_send", gmail.send_approved)


def resolve(session: Session, approval: Approval, *, approve: bool, decided_by: str) -> Approval:
    """Record the owner's yes or no on a pending approval, then carry it out if it can be."""
    if approval.status != "pending":
        raise ApprovalError(409, f"This was already {approval.status}.")
    approval.status = "approved" if approve else "rejected"
    approval.decided_by = decided_by[:100]
    approval.decided_at = datetime.now(UTC)
    payload = dict(approval.payload or {})
    if approve:
        _load_executors()
        executor = EXECUTORS.get(approval.kind)
        payload["result"] = executor(session, approval) if executor else "Approved. Nothing can carry this out yet."
    approval.payload = payload
    audit(session, "policy", f"approval_{approval.status}", kind=approval.kind, approval_id=approval.id, by=decided_by)
    return approval
