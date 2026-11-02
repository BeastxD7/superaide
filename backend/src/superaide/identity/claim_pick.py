"""Two or more contacts share the full name a stranger claimed (issue #492).

Nothing is sent to anyone and the sender's reply is the same as for a name nobody has. Instead the owner gets
one Needs-you card per candidate: "Someone on Telegram says they are Priya Tan. Send a link code to this one?".
Yes (pick) sends a one-time code to that contact's own trusted channel (the same challenge as an unambiguous
claim; the stranger is linked only if they type it there). No on every card is "ignore". Approving one card
closes its siblings, so a single claim can start a single challenge.

The cards are ordinary approvals, so they are sealed, expire, are audited and can be decided only from the
signed-in app. Which person a card is about comes from the database; the claimed text is never shown as such,
only the matching contact's own name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.approvals.binding import seal
from superaide.approvals.models import Approval
from superaide.identity import linking
from superaide.identity.models import ChannelIdentity, Person
from superaide.platform.audit import audit
from superaide.policy.registry import EXECUTORS

KIND = "identity_claim_pick"
MAX_CANDIDATES = 5
COOLDOWN = timedelta(days=7)  # the same account is not put to the owner again for a week, whatever the answer
SIBLING_CLOSED = "system (another contact was picked)"


def _channel_name(provider: str) -> str:
    return {"telegram": "Telegram", "slack": "Slack"}.get(provider, provider.capitalize())


def _open_before(session: Session, identity_id: uuid.UUID, now: datetime) -> bool:
    rows = session.scalars(
        select(Approval).where(Approval.kind == KIND, Approval.created_at > now - COOLDOWN).limit(200)
    ).all()
    return any((a.payload or {}).get("identity_id") == str(identity_id) for a in rows)


def ask_owner(session: Session, identity: ChannelIdentity, claimed: str, people: list[Person]) -> int:
    """Open one card per candidate, once per account per week. Returns how many cards were opened."""
    now = datetime.now(UTC)
    if identity.person_id is not None or "external" in (identity.risk_flags or []):
        return 0
    if _open_before(session, identity.id, now):
        audit(session, "identity", "claim_ambiguous_repeat", identity_id=identity.id)
        return 0
    group = str(uuid.uuid4())
    where = _channel_name(identity.provider)
    said = linking.plain_name(claimed)
    chosen = people[:MAX_CANDIDATES]
    for p in chosen:
        name = linking.plain_name(p.display_name, 80)
        on = sorted({_channel_name(i.provider) for i in linking.trusted_identities(session, p)})
        approval = Approval(
            kind=KIND,
            payload={
                "summary": (
                    f"Someone on {where} says they are {name}. {len(people)} of your contacts have that "
                    f"name. Is this the one ({p.relation}, on {', '.join(on)})? Send a link code to them?"
                ),
                "identity_id": str(identity.id),
                "person_id": str(p.id),
                "provider": identity.provider,
                "claimed_name": said,
                "group": group,
                "candidates": len(chosen),
            },
            requested_by_person_id=None,
        )
        seal(approval)
        session.add(approval)
        session.flush()
        audit(session, "policy", "approval_requested", kind=KIND, approval_id=approval.id)
    audit(session, "identity", "claim_ambiguous", identity_id=identity.id, candidates=len(chosen), group=group)
    return len(chosen)


def _messengers() -> dict[str, object]:
    from superaide.proactive import notify

    return {p: m for p in ("telegram", "slack") if (m := notify.messenger_for(p)) is not None}


def execute(session: Session, approval: Approval) -> str:
    """The owner picked this contact: send the contact a code on their own linked channel."""
    from superaide.identity.vault.store import runtime_settings

    data = approval.payload or {}
    identity = session.get(ChannelIdentity, uuid.UUID(str(data.get("identity_id"))))
    person = session.get(Person, uuid.UUID(str(data.get("person_id"))))
    if identity is None or person is None or person.status != "active":
        return "Not run: that account or contact is gone."
    if identity.person_id is not None:
        return "Not run: that account is already linked to someone."
    for sib in session.scalars(select(Approval).where(Approval.kind == KIND, Approval.status == "pending")).all():
        if sib.id != approval.id and (sib.payload or {}).get("group") == data.get("group"):
            sib.status, sib.decided_by, sib.decided_at = "rejected", SIBLING_CLOSED, datetime.now(UTC)
            sib.payload = {**(sib.payload or {}), "result": "Closed: you picked another contact for this account."}
    settings = runtime_settings()
    challenge = linking.start_challenge(
        session,
        new_identity=identity,
        person=person,
        messengers=_messengers(),  # type: ignore[arg-type]
        settings=settings,
        agent_name=settings.agent_name,
    )
    if challenge is None:
        return f"No code was sent to {person.display_name}: no linked channel is reachable, or a limit was reached."
    return f"A one-time code was sent to {person.display_name} on their own linked channel."


EXECUTORS[KIND] = execute
