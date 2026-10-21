"""Which requested actions may run, need your approval, or are refused.

Authority comes from the person's relation in the database (set by you), never
from what a message claims ("the board approved this", "I'm the CEO"). At the
Infant stage nothing with side effects runs without your approval.
"""

from __future__ import annotations

from dataclasses import dataclass

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
# Identity and trust changes never happen through chat; they need the dashboard or CLI.
NEVER_VIA_CHAT = {"change_identity"}


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
    approval = Approval(
        kind=kind,
        payload={"summary": summary, "trust_at_request": requester.trust, "required_trust": rule.min_trust},
        requested_by_person_id=requester.person.id if requester.person else None,
    )
    session.add(approval)
    session.flush()
    audit(session, "policy", "approval_requested", kind=kind, approval_id=approval.id)
    return Decision("approval", "waiting for your approval", approval)
