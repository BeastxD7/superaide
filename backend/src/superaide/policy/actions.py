"""Which requested actions may run, need your approval, or are refused.

Authority comes from the person's relation in the database (set by you), never
from what a message claims ("the board approved this", "I'm the CEO"). At the
Infant stage nothing with side effects runs without your approval.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
# Actions a skill's own tool asks for (never through propose_action, whose kinds are RULES): the
# tool fills in exactly what would be done, and approving runs it (EXECUTORS).
TOOL_RULES: dict[str, Rule] = {
    "calendar_create_event": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "calendar_move_event": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "calendar_decline_event": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
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


def decide(
    session: Session,
    *,
    kind: str,
    summary: str,
    requester: Sender,
    rules: dict[str, Rule] = RULES,
    payload: dict[str, Any] | None = None,
    origin: str = "",
) -> Decision:
    """`rules` is RULES for propose_action, TOOL_RULES for a skill tool, which also passes the
    `payload` its executor needs. `origin` is "voice" for something heard on a call (voice/gateway.py):
    such an approval can then be approved only from a text channel, never by what was said."""
    if kind == "none":
        return Decision("none", "no action requested")
    if kind in NEVER_VIA_CHAT or kind not in rules:
        audit(session, "policy", "action_refused", kind=kind, reason="not allowed via chat", requester=requester.name)
        return Decision("refused", "not allowed via chat")
    rule = rules[kind]
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
        payload={
            **(payload or {}),
            "summary": summary,
            "trust_at_request": requester.trust,
            "required_trust": rule.min_trust,
            **({"origin": origin} if origin else {}),
        },
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
    import superaide.skills.calendar  # noqa: F401  (registers the calendar executors)
    from superaide.skills import gmail

    EXECUTORS.setdefault("gmail_send", gmail.send_approved)


def resolve(session: Session, approval: Approval, *, approve: bool, decided_by: str, via: str = "") -> Approval:
    """Record the owner's yes or no on a pending approval, then carry it out if it can be.

    `via` is the channel the decision came in on. A yes that came by voice is never accepted, and an
    approval that began on a call is approved only from a text channel the caller names (voice/gateway.py).
    A no is accepted from anywhere: refusing is always safe."""
    if approval.status != "pending":
        raise ApprovalError(409, f"This was already {approval.status}.")
    if approve:
        from superaide.voice.gateway import check_confirmation

        problem = check_confirmation(approval, via)
        if problem:
            audit(session, "policy", "voice_confirmation_refused", approval_id=approval.id, via=via or "unknown")
            raise ApprovalError(409, problem)
    approval.status = "approved" if approve else "rejected"
    approval.decided_by = decided_by[:100]
    approval.decided_at = datetime.now(UTC)
    payload = dict(approval.payload or {})
    if approve:
        from superaide.policy.breaker import allow

        if not allow(session, approval.kind):
            # Too many deletes or sends in a burst: Ava is frozen and this one is left for the owner.
            approval.status = "pending"
            approval.decided_by = None
            approval.decided_at = None
            payload["result"] = "Held: too many sends or deletes in a short time. Ava is paused; review and resume."
            approval.payload = payload
            return approval
        _load_executors()
        executor = EXECUTORS.get(approval.kind)
        payload["result"] = executor(session, approval) if executor else "Approved. Nothing can carry this out yet."
    approval.payload = payload
    audit(session, "policy", f"approval_{approval.status}", kind=approval.kind, approval_id=approval.id, by=decided_by)
    return approval
