"""What happened to an approval, told to the owner and to Ava (issue #340).

The decision used to live only in the `approval` row, so a conversation on another channel still said "waiting
for your approval" after the owner had approved and the mail had gone. Now every decision is written into the
owner's conversation as an outbound note (and pushed to the owner's linked channel by their preference), and
`list_approvals` lets Ava read the rows instead of guessing from the transcript.

The note holds only what the app itself wrote: the kind's label and the result string. The summary is added only
when the request did not come from a turn that read email or the web (`tainted`), so outside text is never
re-quoted into the owner's history.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.db.models import Approval, Person
from superaide.policy.kinds import info

log = logging.getLogger(__name__)

LIST_APPROVALS_TOOL: dict[str, Any] = {
    "name": "list_approvals",
    "description": (
        "The owner's approval requests from the app's records: what is waiting, and what was approved or "
        "rejected with the result (for example 'Sent from your Gmail.'). Use it to answer 'did you send "
        "it?' or 'what is waiting?'; the records override anything said earlier in any chat."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["pending", "approved", "rejected", "any"]},
            "hours": {"type": "integer", "description": "Only those decided or asked in the last N hours (1 to 720)"},
        },
        "additionalProperties": False,
    },
}


def line(a: Approval) -> str:
    p = a.payload or {}
    what = info(a.kind).label if p.get("tainted") else str(p.get("summary") or info(a.kind).label)[:100]
    what = what.replace("\n", " ")
    if a.status == "pending":
        return f"[{a.id}] {a.kind}: {what} -> WAITING for the owner's OK, nothing done yet"
    result = str(p.get("result") or "").replace("\n", " ")[:120]
    when = f" {a.decided_at:%Y-%m-%d %H:%M} UTC" if a.decided_at else ""
    return f"[{a.id}] {a.kind}: {what} -> {a.status.upper()}{when}" + (f": {result}" if result else "")


def list_text(session: Session, args: dict[str, Any]) -> str:
    status = str(args.get("status") or "any")
    q = select(Approval).order_by(Approval.created_at.desc()).limit(30)
    if status in {"pending", "approved", "rejected"}:
        q = q.where(Approval.status == status)
    rows = list(session.scalars(q).all())
    if args.get("hours"):
        cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(int(args["hours"]), 720)))
        rows = [a for a in rows if (a.decided_at or a.created_at) >= cutoff]
    if not rows:
        return "No approval requests match."
    return "\n".join(line(a) for a in rows[:15])


def announce(session: Session, approval: Approval) -> None:
    """Write the outcome into the owner's conversation and push it by their preference. Never raises."""
    if approval.status == "pending" or approval.requested_by_person_id is None:
        return
    try:
        from superaide.proactive.notify import notify_owner

        owner = session.get(Person, approval.requested_by_person_id)
        if owner is None:
            return
        p = approval.payload or {}
        label = info(approval.kind).label
        what = label if p.get("tainted") else f"{label}: {str(p.get('summary') or '')[:90]}".rstrip(": ")
        result = str(p.get("result") or "").strip()
        verb = "approved" if approval.status == "approved" else "rejected"
        text = f"You {verb} “{what}”." + (f" {result}" if result else "")
        notify_owner(
            session,
            owner,
            text,
            {"kind": "approval_outcome", "approval_id": str(approval.id), "status": approval.status},
        )
    except Exception:  # an outcome note must never undo a decision
        log.warning("couldn't announce an approval outcome", exc_info=True)
