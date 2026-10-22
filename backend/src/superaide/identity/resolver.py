"""Resolve a platform sender to a person. Only immutable platform IDs count."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.channels.base import InboundMessage
from superaide.db.models import ChannelIdentity, Person
from superaide.identity.trust import Trust

# Channels whose links are bound to the endpoint (bot or app) that confirmed them.
BOUND_PROVIDERS = ("telegram", "slack")


@dataclass
class Sender:
    identity: ChannelIdentity
    person: Person | None
    trust: int
    # Set when a linked account wrote through an endpoint it wasn't confirmed on ("new_endpoint"), or
    # after a long silence ("stale"). The sender is then treated as unknown until the owner confirms.
    unconfirmed: str = ""
    linked_person: Person | None = None  # who the account is linked to, when unconfirmed
    first_notice: bool = False  # the first message in this unconfirmed state: tell the owner once

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


def endpoint_confirmed(identity: ChannelIdentity, endpoint: str) -> bool:
    """May something private go to this account through this endpoint (bot)?"""
    return not identity.endpoint_key or identity.endpoint_key == endpoint


def resolve(session: Session, msg: InboundMessage, *, reconfirm_after_days: int = 0) -> Sender:
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
        try:
            with session.begin_nested():  # two first messages at once: the loser must not be dropped (BE-14)
                session.add(identity)
                session.flush()
        except IntegrityError:
            existing = find_identity(session, msg.provider, msg.tenant_scope, msg.sender_id)
            if existing is None:
                raise
            identity = existing
        else:
            audit(session, "identity", "new_identity", identity_id=identity.id, provider=msg.provider)
    # Handles are kept for display only.
    identity.aux_handles = {
        **(identity.aux_handles or {}),
        "display_name": msg.sender_display,
        "username": msg.sender_username,
    }
    previous_seen = identity.last_seen_at
    now = datetime.now(UTC)
    if msg.external_sender and "external" not in (identity.risk_flags or []):
        identity.risk_flags = [*(identity.risk_flags or []), "external"]

    person: Person | None = None
    if identity.person_id is not None and identity.lifecycle == "active":
        candidate = session.get(Person, identity.person_id)
        if candidate is not None and candidate.status == "active":
            person = candidate
    if "external" in (identity.risk_flags or []):
        person = None  # people from other companies are never treated as linked colleagues here
    unconfirmed, first = "", False
    if person is not None and msg.provider in BOUND_PROVIDERS:
        unconfirmed, first = _check_endpoint(session, identity, msg, previous_seen, reconfirm_after_days)
    linked = person
    if unconfirmed:
        person = None
        # Kept apart from last_seen_at, so messages through an unconfirmed bot can't refresh the
        # clock that decides when the account must be confirmed again.
        pending = dict((identity.verification or {}).get("pending") or {})
        pending["last_seen_at"] = now.isoformat()
        identity.verification = {**(identity.verification or {}), "pending": pending}
    else:
        identity.last_seen_at = now
    trust = identity.trust_level if person else min(identity.trust_level, int(Trust.AUTHENTICATED))
    return Sender(
        identity=identity,
        person=person,
        trust=trust,
        unconfirmed=unconfirmed,
        linked_person=linked if unconfirmed else None,
        first_notice=first,
    )


def _check_endpoint(
    session: Session, identity: ChannelIdentity, msg: InboundMessage, previous_seen: datetime | None, days: int
) -> tuple[str, bool]:
    """Is this linked account writing through the endpoint its link was confirmed on?

    Returns (reason, first): reason "" when it is; "new_endpoint" through another bot or app;
    "stale" when it comes back after `days` of silence. `first` is True when this message started
    the unconfirmed state (so the owner is told once, not on every message)."""
    if not identity.endpoint_key:
        if msg.endpoint:
            # Linked before links were bound (or added by the operator): bind to the endpoint the
            # owner set up. Messages only ever arrive through the bot or app connected in the app.
            identity.endpoint_key = msg.endpoint
            audit(
                session,
                "identity",
                "endpoint_bound",
                identity_id=identity.id,
                endpoint=msg.endpoint,
                method="first_use",
            )
        return "", False
    pending = (identity.verification or {}).get("pending")
    reason = ""
    if msg.endpoint != identity.endpoint_key:
        reason = "new_endpoint"
    elif isinstance(pending, dict) and pending.get("endpoint") == msg.endpoint:
        reason = str(pending.get("reason") or "stale")
    elif days > 0 and previous_seen is not None and datetime.now(UTC) - previous_seen > timedelta(days=days):
        reason = "stale"
    if not reason:
        return "", False
    first = not (isinstance(pending, dict) and pending.get("endpoint") == msg.endpoint)
    if first:
        identity.verification = {
            **(identity.verification or {}),
            "pending": {"endpoint": msg.endpoint, "reason": reason, "since": datetime.now(UTC).isoformat()},
        }
    audit(
        session, "identity", "link_unconfirmed", identity_id=identity.id, provider=msg.provider, reason=reason,
        endpoint=msg.endpoint, confirmed_endpoint=identity.endpoint_key,
    )  # fmt: skip
    return reason, first
