"""Which requested actions may run, need your approval, or are refused.

Authority comes from the person's relation in the database (set by you), never
from what a message claims ("the board approved this", "I'm the CEO"). At the
Infant stage nothing with side effects runs without your approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from superaide.contracts.events import ApprovalDecided, bus
from superaide.identity.resolver import Sender
from superaide.identity.trust import Trust
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.models import Approval
from superaide.policy import footprint, kinds, outcomes, quote
from superaide.policy.binding import EXPIRED_BY, check, expired, seal
from superaide.policy.breaker import allow
from superaide.policy.confirmation import check_confirmation
from superaide.policy.registry import (
    CREATION_RULES,
    DISCARDERS,
    EXECUTORS,
    NEVER_VIA_CHAT,
    RULES,
    TOOL_RULES,
    Rule,
)

__all__ = [
    "CREATION_RULES",
    "DISCARDERS",
    "EXECUTORS",
    "NEVER_VIA_CHAT",
    "RULES",
    "TOOL_RULES",
    "ApprovalError",
    "Decision",
    "Rule",
    "action_kinds",
    "decide",
    "resolve",
]


def action_kinds() -> list[str]:
    """The kinds the model may propose through propose_action."""
    offered = set(RULES)
    if get_settings().footprint_guard_enabled:
        offered |= set(CREATION_RULES)
    return sorted(offered)


@dataclass
class Decision:
    outcome: str  # approval | refused | none
    reason: str
    approval: Approval | None = None
    unsupported: bool = False  # refused because nothing can carry this kind or route out (#326)


def decide(
    session: Session,
    *,
    kind: str,
    summary: str,
    requester: Sender,
    rules: dict[str, Rule] = RULES,
    payload: dict[str, Any] | None = None,
    origin: str = "",
    require_executor: bool = False,
    in_channel: bool = False,
    tainted: bool = False,
) -> Decision:
    """`rules` is RULES for propose_action, TOOL_RULES for a skill tool, which also passes the
    `payload` its executor needs. `origin` is "voice" for something heard on a call (voice/gateway.py):
    such an approval can then be approved only from the Superaide app, never by what was said.
    `require_executor` (the model's propose_action): a kind nothing can carry out, or a route nothing
    can use (a web form), creates no approval at all (issue #326, APPROVAL_REQUIRE_EXECUTOR).
    `tainted`: untrusted content entered the turn; it only feeds the mandate shadow check (#112)."""
    if kind == "none":
        return Decision("none", "no action requested")
    if rules is RULES and footprint.enabled():
        rules = {**RULES, **CREATION_RULES}
    if kind in NEVER_VIA_CHAT or kind not in rules:
        audit(session, "policy", "action_refused", kind=kind, reason="not allowed via chat", requester=requester.name)
        return Decision("refused", "not allowed via chat")
    rule = rules[kind]
    if require_executor and get_settings().approval_require_executor:
        if not kinds.has_executor(kind) or kinds.unsupported_route(summary):
            audit(session, "policy", "action_refused", kind=kind, reason="no executor", requester=requester.name)
            return Decision("refused", kinds.unsupported_message(kind, summary), unsupported=True)
    if in_channel and rule.min_trust >= int(Trust.STEPPED_UP):
        # Said in a channel others can read: approval-type actions still queue a card for the signed-in app,
        # but the ones that need a stronger identity check than a chat account are for a direct message or the app.
        audit(
            session,
            "policy",
            "action_refused",
            kind=kind,
            reason="needs a private conversation",
            requester=requester.name,
        )
        return Decision("refused", "ask me in a direct message, not in a channel")
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
    from superaide.mandate import service as mandate

    seen = mandate.shadow(session, kind=kind, payload=payload, tainted=tainted)  # a measurement; asks exactly as before
    approval = Approval(
        kind=kind,
        payload={
            **(payload or {}),
            **extra,
            **quote.assess(session, kind),
            **({"mandate_shadow": seen} if seen is not None else {}),
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
    """Decide an approval (see `_resolve`), then write the outcome into the owner's conversation (#340)."""
    was_pending = approval.status == "pending"
    done = _resolve(session, approval, approve=approve, decided_by=decided_by, via=via)
    if was_pending and done.status != "pending":
        outcomes.announce(session, done)
    return done


def _resolve(session: Session, approval: Approval, *, approve: bool, decided_by: str, via: str = "") -> Approval:
    """Record the owner's yes or no on a pending approval, then carry it out if it can be.

    `via` is the channel the decision came in on. A yes that came by voice is never accepted, and an
    approval that began on a call is approved only from the Superaide app (voice/gateway.py).
    A no is accepted from anywhere: refusing is always safe."""
    if approval.status != "pending":
        raise ApprovalError(409, f"This was already {approval.status}.")
    if approve:
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
    discard = DISCARDERS.get(approval.kind)
    if not approve and discard:
        discard(session, approval)  # a no leaves nothing behind (gmail_send: the owner's Gmail draft)
    if approve:
        blocked = _binding_blocks(session, approval)
        if blocked:
            payload["result"] = blocked
            approval.payload = payload
            audit(session, "policy", "approval_approved", kind=approval.kind, approval_id=approval.id, by=decided_by,
                  ran=False)  # fmt: skip
            return approval
        if not allow(session, approval.kind):
            # Too many deletes or sends in a burst: Ava is frozen and this one is left for the owner.
            approval.status = "pending"
            approval.decided_by = None
            approval.decided_at = None
            payload["result"] = "Held: too many sends or deletes in a short time. Ava is paused; review and resume."
            approval.payload = payload
            return approval
        if quote.enabled() and quote.prepare_only(approval):
            payload["result"] = (
                "Prepared, not run: the odds for this are low. The details are on the card; do the last step yourself."
            )
        else:
            _log_executed(session, approval)
            executor = EXECUTORS.get(approval.kind)
            payload["result"] = (
                executor(session, approval)
                if executor
                else "Approved, but this will not do anything: nothing can carry this out yet."
            )
            if executor:
                payload["outcome"] = quote.outcome_of(payload["result"])
    approval.payload = payload
    audit(session, "policy", f"approval_{approval.status}", kind=approval.kind, approval_id=approval.id, by=decided_by)
    bus.publish(ApprovalDecided(session, approval, approve))  # skills.receipts (#131) writes a content-free receipt
    return approval
