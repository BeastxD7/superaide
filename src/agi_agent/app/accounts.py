"""Hiring the agent, app sign-in sessions and channel invites.

The person who hires the agent becomes its manager. Their browser gets a
session token (only its hash is stored). The in-app chat is a channel like
Telegram or Slack, keyed on the person's own ID, which only the server can mint.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from agi_agent.audit import audit
from agi_agent.channels.base import InboundMessage
from agi_agent.db.models import AppSession, ChannelIdentity, ChannelInvite, IdentityLinkEvent, Onboarding, Person
from agi_agent.identity.trust import Trust

INVITE_TTL_MINUTES = 15
NOTICE_VERSION = "2026-10-21"
NOTICE_TEXT = (
    "Ava is an AI. It remembers what you tell it so it can work with you, shows everything it remembers "
    "in its notebook, and never repeats private things to anyone else. You can remove anything at any time."
)


class AlreadyHired(Exception):
    def __init__(self, owner: str) -> None:
        super().__init__(owner)
        self.owner = owner


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def owner(session: Session) -> Person | None:
    return session.scalar(
        select(Person).where(Person.relation == "manager", Person.status == "active").order_by(Person.created_at)
    )


def hire(session: Session, *, name: str) -> tuple[Person, str]:
    existing = owner(session)
    if existing is not None:
        raise AlreadyHired(existing.display_name)
    now = datetime.now(UTC)
    person = Person(
        display_name=name.strip()[:80],
        relation="manager",
        notice_basis={"kind": "consent_at_hire", "version": NOTICE_VERSION, "text": NOTICE_TEXT, "at": now.isoformat()},
        channel_prefs={"routine": "web"},
    )
    session.add(person)
    session.flush()
    identity = ChannelIdentity(
        person_id=person.id,
        provider="web",
        tenant_scope="",
        stable_key=str(person.id),
        trust_level=int(Trust.LINKED),
        verification={"method": "account_created_at_hire", "verified_at": now.isoformat()},
        aux_handles={"display_name": person.display_name},
        last_seen_at=now,
    )
    session.add(identity)
    session.flush()
    session.add(
        IdentityLinkEvent(
            identity_id=identity.id,
            person_id=person.id,
            action="link",
            method="account_created_at_hire",
            actor="app",
        )
    )
    session.add(Onboarding(person_id=person.id, step="hello", answers={}))
    token = new_session(session, person)
    audit(session, "app", "hired", person_id=person.id)
    return person, token


def new_session(session: Session, person: Person) -> str:
    token = secrets.token_urlsafe(32)
    session.add(AppSession(person_id=person.id, token_hash=_hash(token)))
    return token


def person_for_token(session: Session, token: str) -> Person | None:
    if not token:
        return None
    row = session.scalar(select(AppSession).where(AppSession.token_hash == _hash(token), AppSession.revoked.is_(False)))
    if row is None:
        return None
    row.last_used_at = datetime.now(UTC)
    person = session.get(Person, row.person_id)
    return person if person is not None and person.status == "active" else None


def sign_out(session: Session, token: str) -> None:
    row = session.scalar(select(AppSession).where(AppSession.token_hash == _hash(token)))
    if row is not None:
        row.revoked = True


def web_message(person: Person, text: str) -> InboundMessage:
    """A message typed in the signed-in app. Verified because the session token was."""
    return InboundMessage(
        provider="web",
        tenant_scope="",
        sender_id=str(person.id),
        chat_id=f"web:{person.id}",
        chat_kind="dm",
        text=text,
        verified=True,
        sender_display=person.display_name,
    )


def linked_providers(session: Session, person: Person) -> set[str]:
    rows = session.scalars(
        select(ChannelIdentity.provider).where(
            ChannelIdentity.person_id == person.id, ChannelIdentity.lifecycle == "active"
        )
    ).all()
    return set(rows)


# --- Telegram invites: made in the signed-in app, redeemed in Telegram ---------------


def create_invite(session: Session, person: Person, provider: str) -> str:
    code = secrets.token_urlsafe(12)  # letters, digits, - and _: valid in a Telegram start link
    session.add(
        ChannelInvite(
            person_id=person.id,
            provider=provider,
            code_hash=_hash(code),
            expires_at=datetime.now(UTC) + timedelta(minutes=INVITE_TTL_MINUTES),
        )
    )
    audit(session, "app", "channel_invite_created", person_id=person.id, provider=provider)
    return code


def redeem_invite(session: Session, *, provider: str, code: str, identity: ChannelIdentity) -> Person | None:
    """Link the account that opened the invite to the person who made it."""
    now = datetime.now(UTC)
    invite = session.scalar(
        select(ChannelInvite).where(ChannelInvite.code_hash == _hash(code), ChannelInvite.provider == provider)
    )
    if invite is None or invite.used_at is not None or invite.expires_at <= now:
        audit(session, "identity", "invite_rejected", provider=provider)
        return None
    if "external" in (identity.risk_flags or []):
        audit(session, "identity", "link_refused", reason="external_account", identity_id=identity.id)
        return None
    person = session.get(Person, invite.person_id)
    if person is None:
        return None
    invite.used_at = now
    invite.used_by_identity_id = identity.id
    identity.person_id = person.id
    identity.trust_level = int(Trust.LINKED)
    identity.verification = {"method": "invite_from_app", "verified_at": now.isoformat(), "invite_id": str(invite.id)}
    session.add(
        IdentityLinkEvent(
            identity_id=identity.id,
            person_id=person.id,
            action="link",
            method="invite_from_app",
            actor="identity_service",
            evidence={"invite_id": str(invite.id)},
        )
    )
    audit(session, "identity", "linked", identity_id=identity.id, person_id=person.id, method="invite_from_app")
    return person
