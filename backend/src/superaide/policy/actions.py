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
from superaide.config import get_settings
from superaide.db.models import Approval
from superaide.identity.resolver import Sender
from superaide.identity.trust import Trust
from superaide.policy.binding import EXPIRED_BY, check, expired, seal


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
# Footprint Guard (#136): things created in the world under the owner's name. Only offered and accepted when
# FOOTPRINT_GUARD_ENABLED; always manager-only and always approved by the owner first.
CREATION_RULES: dict[str, Rule] = {
    kind: Rule(int(Trust.LINKED), manager_only=True, needs_approval=True)
    for kind in ("create_account", "create_profile", "publish_post", "open_pull_request", "post_review")
}


def action_kinds() -> list[str]:
    """The kinds the model may propose through propose_action."""
    from superaide.config import get_settings

    kinds = set(RULES)
    if get_settings().footprint_guard_enabled:
        kinds |= set(CREATION_RULES)
    return sorted(kinds)


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
    from superaide.policy import footprint

    if rules is RULES and footprint.enabled():
        rules = {**RULES, **CREATION_RULES}
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
    extra = footprint.assess(session, kind=kind, text=summary, requester=requester)
    from superaide.policy import quote

    approval = Approval(
        kind=kind,
        payload={
            **(payload or {}),
            **extra,
            **quote.assess(session, kind),
            "summary": summary,
            "trust_at_request": requester.trust,
            "required_trust": rule.min_trust,
            **({"origin": origin} if origin else {}),
        },
        requested_by_person_id=requester.person.id if requester.person else None,
    )
    seal(approval)
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


def _binding_blocks(session: Session, approval: Approval) -> str | None:
    """Re-derive the hash of what is about to run. In `strict` a difference stops it; in `record` it is only
    audited (issue #111); `off` skips the check."""
    mode = get_settings().approval_binding
    if mode == "off":
        return None
    state = check(approval)
    if state == "ok":
        return None
    audit(
        session, "policy",
        "approval_payload_mismatch" if state == "mismatch" else "approval_unsealed",
        approval_id=approval.id, kind=approval.kind, mode=mode, layer="payload",
    )  # fmt: skip
    if mode == "strict":
        if state == "unsealed":  # made before the check existed (or while it was off): can't be verified, so ask again
            return (
                "Not run: this request was made before approvals were checked, so it can't be verified. Ask Ava again."
            )
        return "Not run: this changed after you approved it. Ask Ava again."
    return None


def _log_executed(session: Session, approval: Approval) -> None:
    from superaide.actionlog import chain
    from superaide.tooltrace.effects import effect_of

    tool = approval.kind
    chain.append(session, kind="executed", tool=tool, effect=effect_of(tool), approval_id=approval.id)


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
    approval.decided_at = datetime.now(UTC)
    payload = dict(approval.payload or {})
    if approve and expired(approval, now=approval.decided_at):
        # A yes to a card that has gone stale is not a yes to what would happen now.
        approval.status, approval.decided_by = "rejected", EXPIRED_BY
        payload["result"] = "Not run: this request expired before it was answered. Ask Ava again."
        approval.payload = payload
        audit(session, "policy", "approval_expired", kind=approval.kind, approval_id=approval.id)
        return approval
    approval.status = "approved" if approve else "rejected"
    approval.decided_by = decided_by[:100]
    if approve:
        blocked = _binding_blocks(session, approval)
        if blocked:
            payload["result"] = blocked
            approval.payload = payload
            audit(session, "policy", "approval_approved", kind=approval.kind, approval_id=approval.id, by=decided_by,
                  ran=False)  # fmt: skip
            return approval
        from superaide.policy.breaker import allow

        if not allow(session, approval.kind):
            # Too many deletes or sends in a burst: Ava is frozen and this one is left for the owner.
            approval.status = "pending"
            approval.decided_by = None
            approval.decided_at = None
            payload["result"] = "Held: too many sends or deletes in a short time. Ava is paused; review and resume."
            approval.payload = payload
            return approval
        from superaide.policy import quote

        if quote.enabled() and quote.prepare_only(approval):
            payload["result"] = (
                "Prepared, not run: the odds for this are low. The details are on the card; do the last step yourself."
            )
        else:
            _load_executors()
            _log_executed(session, approval)
            executor = EXECUTORS.get(approval.kind)
            payload["result"] = executor(session, approval) if executor else "Approved. Nothing can carry this out yet."
            if executor:
                payload["outcome"] = quote.outcome_of(payload["result"])
    approval.payload = payload
    audit(session, "policy", f"approval_{approval.status}", kind=approval.kind, approval_id=approval.id, by=decided_by)
    return approval
