"""Resolve a platform sender to a person. Only immutable platform IDs count."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.channels.base import InboundMessage
from superaide.db.models import ChannelIdentity, Person
from superaide.identity.trust import Trust


@dataclass
class Sender:
    identity: ChannelIdentity
    person: Person | None
    trust: int

    @property
    def known(self) -> bool:
        return self.person is not None

    @property
    def relation(self) -> str:
        return self.person.relation if self.person else "unknown"

    @property
    def name(self) -> str:
        return self.person.display_name if self.person else "someone I don't know yet"


def find_identity(session: Session, provider: str, tenant_scope: str, stable_key: str) -> ChannelIdentity | None:
    return session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.provider == provider,
            ChannelIdentity.tenant_scope == tenant_scope,
            ChannelIdentity.stable_key == stable_key,
        )
    )


def resolve(session: Session, msg: InboundMessage) -> Sender:
    if not msg.verified:
        raise ValueError("refusing to resolve an unverified message")
    identity = find_identity(session, msg.provider, msg.tenant_scope, msg.sender_id)
    if identity is None:
        identity = ChannelIdentity(
            provider=msg.provider,
            tenant_scope=msg.tenant_scope,
            stable_key=msg.sender_id,
            trust_level=int(Trust.AUTHENTICATED),
            risk_flags=["external"] if msg.external_sender else [],
            aux_handles={},
        )
        session.add(identity)
        session.flush()
        audit(session, "identity", "new_identity", identity_id=identity.id, provider=msg.provider)
    # Handles are kept for display only.
    identity.aux_handles = {
        **(identity.aux_handles or {}),
        "display_name": msg.sender_display,
        "username": msg.sender_username,
    }
    identity.last_seen_at = datetime.now(UTC)
    if msg.external_sender and "external" not in (identity.risk_flags or []):
        identity.risk_flags = [*(identity.risk_flags or []), "external"]

    person: Person | None = None
    if identity.person_id is not None and identity.lifecycle == "active":
        candidate = session.get(Person, identity.person_id)
        if candidate is not None and candidate.status == "active":
            person = candidate
    if "external" in (identity.risk_flags or []):
        person = None  # people from other companies are never treated as linked colleagues here
    trust = identity.trust_level if person else min(identity.trust_level, int(Trust.AUTHENTICATED))
    return Sender(identity=identity, person=person, trust=trust)
