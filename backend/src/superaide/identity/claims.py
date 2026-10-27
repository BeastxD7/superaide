"""Someone unknown whose name is an existing contact's name (issue #93).

A name is not an identity. When a sender whose platform account is not linked to anyone has a display name, or
says a name, that exactly matches (normalised) a person Ava already knows, they are treated as an unverified
stranger, never as that person. Ava does not decide who they are: she asks the OWNER in the app, with only what
the platform told us, and the owner's explicit answer is the only thing that links the account. Until then the
sender gets the same neutral stranger reply as anyone else, so nothing about the match shows from outside.

Controlled by IDENTITY_CLAIM_REVIEW (off by default). Nothing here reads message content to grant anything:
the claimed name is only used to find which contact to ask the owner about.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.channels.base import InboundMessage
from superaide.identity.linking import detect_claimed_name, plain_name
from superaide.identity.trust import Trust
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.models import ChannelIdentity, IdentityClaim, IdentityLinkEvent, Person

CHOICES = ("contact", "new", "stranger", "block")
RELATIONS = ("colleague", "contact", "family", "partner", "friend", "client", "other")
COOLDOWN = timedelta(days=7)  # after "No / don't know": the same account is not asked about again for a week
EXCERPT = 160


def normalise(name: str) -> str:
    """Full name, compared as: Unicode-folded, case-folded, punctuation dropped, spaces collapsed."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("'", "").replace("\u2019", "")  # O'Brien and OBrien are one name
    return " ".join(re.sub(r"[^\w\s]|_", " ", text).split())


def enabled() -> bool:
    return get_settings().identity_claim_review


def match(session: Session, names: list[str]) -> tuple[Person, str] | None:
    """The existing person whose normalised full name equals one of these (a first name alone is not enough)."""
    wanted = {normalise(n): n for n in names if normalise(n)}
    if not wanted:
        return None
    for p in session.scalars(select(Person).where(Person.status == "active").order_by(Person.created_at)):
        key = normalise(p.display_name)
        if key and key in wanted:
            return p, wanted[key]
    return None


def _phone(msg: InboundMessage) -> str:
    """Only what the platform itself supplied; there is nothing to guess from message text."""
    raw = msg.raw or {}
    candidates: list[Any] = [
        raw.get("phone"),
        (raw.get("contact") or {}).get("phone_number") if isinstance(raw.get("contact"), dict) else None,
        ((raw.get("user") or {}).get("profile") or {}).get("phone") if isinstance(raw.get("user"), dict) else None,
    ]
    return next((re.sub(r"[^\d+]", "", str(c))[:20] for c in candidates if c), "")


def _excerpt(text: str) -> str:
    return " ".join(str(text or "").split())[:EXCERPT]


def facts_of(identity: ChannelIdentity, msg: InboundMessage) -> dict[str, Any]:
    """Everything we actually know, taken from the platform and the message. Nothing a model wrote."""
    return {
        "display_name": plain_name(msg.sender_display or (identity.aux_handles or {}).get("display_name") or ""),
        "provider": msg.provider,
        "platform_user_id": identity.stable_key,
        "handle": plain_name(msg.sender_username or (identity.aux_handles or {}).get("username") or ""),
        "phone": _phone(msg),
        "first_message_at": datetime.now(UTC).isoformat(),
        "excerpt": _excerpt(msg.text),
    }


def claim_for(session: Session, identity: ChannelIdentity) -> IdentityClaim | None:
    return session.scalar(select(IdentityClaim).where(IdentityClaim.identity_id == identity.id))


def is_blocked(session: Session, identity: ChannelIdentity) -> bool:
    row = claim_for(session, identity)
    return row is not None and row.status == "blocked"


def review(session: Session, identity: ChannelIdentity, msg: InboundMessage) -> IdentityClaim | None:
    """Called for an unknown sender's direct message. Opens (once) the question for the owner when the sender's
    name is a known person's name. Returns the claim, or None when there is nothing to ask. Never links."""
    if not enabled() or identity.person_id is not None:
        return None
    claimed = detect_claimed_name(msg.text) or ""
    found = match(session, [msg.sender_display, claimed])
    if found is None:
        return None
    person, said = found
    now = datetime.now(UTC)
    row = claim_for(session, identity)
    if row is not None:
        if row.status == "open":
            row.asks += 1  # the same sender again: counted, not asked again
        elif row.status == "stranger" and row.decided_at and now - row.decided_at > COOLDOWN:
            row.status, row.decided_at, row.decided_by = "open", None, None
            row.matched_person_id, row.claimed_name, row.facts = person.id, plain_name(said), facts_of(identity, msg)
            row.asks += 1
            _tell_owner(session, row, person, now)
        return row
    row = IdentityClaim(
        identity_id=identity.id,
        matched_person_id=person.id,
        claimed_name=plain_name(said),
        facts=facts_of(identity, msg),
        asks=1,
    )
    session.add(row)
    session.flush()
    audit(session, "identity", "claim_review_opened", claim_id=row.id, identity_id=identity.id, person_id=person.id)
    _tell_owner(session, row, person, now)
    return row


def _tell_owner(session: Session, row: IdentityClaim, person: Person, now: datetime) -> None:
    """One short push at most, and no more than CLAIM_QUESTIONS_PER_DAY a day for the whole instance. The
    details and the answer buttons are in the app (Needs you); the claim is listed there either way."""
    from superaide.app.accounts import owner
    from superaide.proactive.notify import notify_owner

    limit = get_settings().claim_questions_per_day
    sent = session.scalar(
        select(func.count())
        .select_from(IdentityClaim)
        .where(IdentityClaim.notified_at.is_not(None), IdentityClaim.notified_at > now - timedelta(days=1))
    )
    boss = owner(session)
    if boss is None or (sent or 0) >= limit:
        audit(session, "identity", "claim_review_not_pushed", claim_id=row.id, reason="limit" if boss else "no owner")
        return
    row.notified_at = now
    notify_owner(
        session,
        boss,
        f"Someone is asking about you and says they are {row.claimed_name or person.display_name}. Do you know "
        "them? Open Needs you to see what we know and answer.",
        {"kind": "identity_claim", "claim_id": str(row.id)},
        channel="default",
    )


def view(session: Session, row: IdentityClaim) -> dict[str, Any]:
    person = session.get(Person, row.matched_person_id) if row.matched_person_id else None
    return {
        "id": str(row.id),
        "status": row.status,
        "claimed_name": row.claimed_name,
        "contact": None if person is None else {"id": str(person.id), "name": person.display_name,
                                                "relation": person.relation},
        "facts": dict(row.facts or {}),
        "asks": row.asks,
        "created_at": row.created_at.isoformat(),
        "question": f"Someone is asking about you and says they are {row.claimed_name or '(a name)'}. "
        "Do you know them?",
        "choices": [
            {
                "id": "contact",
                "label": f"Yes, that's {person.display_name} (link this account)" if person else "",
            },
            {"id": "new", "label": "Yes, someone new (add as a person)"},
            {"id": "stranger", "label": "No / I don't know them (keep as a stranger)"},
            {"id": "block", "label": "Block"},
        ],
    }  # fmt: skip


class ClaimError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def answer(
    session: Session, row: IdentityClaim, *, choice: str, decided_by: Person, relation: str = "contact"
) -> IdentityClaim:
    """The owner's explicit answer. The only way a claim links an account."""
    from superaide.memory.knowledge.store import add_contact

    if row.status != "open":
        raise ClaimError(409, f"This was already answered ({row.status}).")
    if choice not in CHOICES:
        raise ClaimError(422, "Choose one of the answers shown.")
    identity = session.get(ChannelIdentity, row.identity_id)
    if identity is None:
        raise ClaimError(404, "That account is gone.")
    now = datetime.now(UTC)
    target: Person | None = None
    if choice in {"contact", "new"} and "external" in (identity.risk_flags or []):
        raise ClaimError(409, "Accounts from another company can't be linked here.")
    if choice == "contact":
        target = session.get(Person, row.matched_person_id) if row.matched_person_id else None
        if target is None or target.status != "active":
            raise ClaimError(409, "That contact isn't there any more.")
        status, method = "linked", "owner_confirmed_claim"
    elif choice == "new":
        if relation not in RELATIONS:
            raise ClaimError(422, "Choose how you know them.")
        name = plain_name((row.facts or {}).get("display_name") or row.claimed_name) or "New contact"
        contact = add_contact(session, owner=decided_by, name=name, relationship=relation, separate=True)
        target = session.get(Person, contact.person_id)
        status, method = "new_person", "owner_added_claim"
    elif choice == "block":
        status, method = "blocked", ""
    else:
        status, method = "stranger", ""
    if target is not None:
        identity.person_id = target.id
        identity.trust_level = int(Trust.LINKED)
        identity.verification = {"method": method, "verified_at": now.isoformat(), "claim_id": str(row.id)}
        session.add(
            IdentityLinkEvent(
                identity_id=identity.id, person_id=target.id, action="link", method=method,
                actor=f"owner:{decided_by.id}", evidence={"claim_id": str(row.id)},
            )
        )  # fmt: skip
    row.status, row.decided_at, row.decided_by = status, now, str(decided_by.id)
    audit(session, "owner", f"claim_{status}", claim_id=row.id, identity_id=identity.id,
          person_id=target.id if target else None)  # fmt: skip
    return row


def open_claims(session: Session, limit: int = 50) -> list[IdentityClaim]:
    return list(
        session.scalars(
            select(IdentityClaim)
            .where(IdentityClaim.status == "open")
            .order_by(IdentityClaim.created_at.desc())
            .limit(limit)
        )
    )


def get(session: Session, claim_id: uuid.UUID) -> IdentityClaim | None:
    return session.get(IdentityClaim, claim_id)
