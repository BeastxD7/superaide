"""Hiring the agent, and connecting the owner's other channels.

Hiring needs a signed-in account (see superaide.identity.auth). The account's Person
becomes the agent's manager. The in-app chat is a channel like Telegram or
Slack, keyed on the person's own ID, which only the server can mint.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.channels.base import InboundMessage
from superaide.identity.trust import Trust
from superaide.platform.audit import audit
from superaide.platform.db.models import (
    AgentHire,
    ChannelIdentity,
    ChannelInvite,
    IdentityLinkEvent,
    Person,
    UserAccount,
)

INVITE_TTL_MINUTES = 15
NOTICE_VERSION = "2026-10-21"
NOTICE_TEXT = (
    "Ava is an AI personal assistant. It remembers what you tell it, and what people who contact it say, so "
    "it can help you. Everything it knows about you is in its notebook. It shares only what you allow, never "
    "repeats private things to anyone else, and you can remove anything at any time."
)


class AlreadyHired(Exception):
    def __init__(self, owner: str) -> None:
        super().__init__(owner)
        self.owner = owner


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def active_hire(session: Session, agent_slug: str = "ava") -> AgentHire | None:
    """The hire behind the instance in scope."""
    found = owner_account(session, agent_slug)
    if found is None:
        return None
    return session.scalar(
        select(AgentHire).where(
            AgentHire.agent_slug == agent_slug, AgentHire.owner_user_id == found.id, AgentHire.status == "active"
        )
    )


def owner_account(session: Session, agent_slug: str = "ava") -> UserAccount | None:
    """The account that hired the agent instance in scope (ADR 0009), if anyone has."""
    from superaide.platform.tenancy.service import instance_owner

    found = instance_owner(session)
    return found[0] if found else None


def owner(session: Session, agent_slug: str = "ava") -> Person | None:
    """The Person who hired the agent instance in scope, if anyone has: its owner, as seen inside it."""
    from superaide.platform.tenancy.service import instance_owner

    found = instance_owner(session)
    return found[1] if found else None


def anyone_hired(session: Session, agent_slug: str = "ava") -> bool:
    """Whether any account has hired this agent, on the whole server."""
    from superaide.platform.tenancy.models import AgentInstance

    return (
        session.scalar(
            select(AgentInstance.id).where(AgentInstance.agent_slug == agent_slug, AgentInstance.status == "active")
        )
        is not None
    )


def hire(session: Session, user: UserAccount, agent_slug: str = "ava") -> Person:
    """Hire the agent for this account: its own instance (ADR 0009), with the account's Person
    inside it as the manager. Hiring again returns the same Person."""
    from superaide.platform.db.session import rescope
    from superaide.platform.tenancy.context import use_instance
    from superaide.platform.tenancy.service import AlreadyTaken, place_hire

    try:
        scope = place_hire(session, user, agent_slug)
    except AlreadyTaken as exc:
        raise AlreadyHired(exc.owner_name) from exc
    with use_instance(scope):
        rescope(session)
        person = _hire_inside(session, user, agent_slug)
    rescope(session)
    return person


def _hire_inside(session: Session, user: UserAccount, agent_slug: str) -> Person:
    from superaide.platform.tenancy.context import require_scope
    from superaide.platform.tenancy.service import join_as_owner

    existing = session.scalar(
        select(AgentHire).where(
            AgentHire.agent_slug == agent_slug, AgentHire.owner_user_id == user.id, AgentHire.status == "active"
        )
    )
    person = join_as_owner(session, user, require_scope())
    if existing is not None:
        return person
    now = datetime.now(UTC)
    notice = {"kind": "consent_at_hire", "version": NOTICE_VERSION, "text": NOTICE_TEXT, "at": now.isoformat()}
    session.add(AgentHire(agent_slug=agent_slug, owner_user_id=user.id, notice=notice))
    person.relation = "manager"
    person.notice_basis = notice
    session.flush()
    identity = session.scalar(
        select(ChannelIdentity).where(ChannelIdentity.provider == "web", ChannelIdentity.stable_key == str(person.id))
    )
    if identity is None:
        identity = ChannelIdentity(
            person_id=person.id,
            provider="web",
            tenant_scope="",
            stable_key=str(person.id),
            trust_level=int(Trust.LINKED),
            verification={"method": "account_signed_in", "verified_at": now.isoformat()},
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
                method="account_signed_in",
                actor="app",
            )
        )
    # The introduction's row is made when it starts (app/onboarding.py), where it is known whether the first
    # step is choosing Ava's brain.
    audit(session, "app", "hired", person_id=person.id, user_id=user.id, agent=agent_slug)
    return person


def instance_of(session: Session, user_id: uuid.UUID, agent_slug: str = "ava"):
    """(scope, person_id) of the account's hired instance of this agent, or None."""
    from superaide.platform.tenancy.service import membership, scope_of

    found = membership(session, user_id, agent_slug)
    return (scope_of(found[0]), found[1].person_id) if found else None


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


def create_invite(
    session: Session, person: Person, provider: str, *, endpoint: str, for_identity: ChannelIdentity | None = None
) -> str:
    """`endpoint` is the bot the link opens (its id); the invite works only through that bot.
    `for_identity` makes it a confirm-this-bot invite that only that account can use."""
    code = secrets.token_urlsafe(12)  # letters, digits, - and _: valid in a Telegram start link
    session.add(
        ChannelInvite(
            person_id=person.id,
            provider=provider,
            code_hash=_hash(code),
            expires_at=datetime.now(UTC) + timedelta(minutes=INVITE_TTL_MINUTES),
            endpoint_key=endpoint,
            for_identity_id=for_identity.id if for_identity else None,
        )
    )
    audit(
        session, "app", "channel_invite_created", person_id=person.id, provider=provider, endpoint=endpoint,
        confirm=for_identity is not None,
    )  # fmt: skip
    return code


def redeem_invite(
    session: Session, *, provider: str, code: str, identity: ChannelIdentity, endpoint: str = ""
) -> Person | None:
    """Link the account that opened the invite to the person who made it, on the bot it came through."""
    now = datetime.now(UTC)
    invite = session.scalar(
        select(ChannelInvite).where(ChannelInvite.code_hash == _hash(code), ChannelInvite.provider == provider)
    )
    if invite is None or invite.used_at is not None or invite.expires_at <= now:
        audit(session, "identity", "invite_rejected", provider=provider)
        return None
    if invite.endpoint_key and invite.endpoint_key != endpoint:
        audit(session, "identity", "invite_rejected", provider=provider, reason="wrong_endpoint", invite_id=invite.id)
        return None
    if invite.for_identity_id is not None and invite.for_identity_id != identity.id:
        audit(session, "identity", "invite_rejected", provider=provider, reason="other_account", invite_id=invite.id)
        return None
    if "external" in (identity.risk_flags or []):
        audit(session, "identity", "link_refused", reason="external_account", identity_id=identity.id)
        return None
    person = session.get(Person, invite.person_id)
    if person is None:
        return None
    relink = identity.person_id == person.id  # already theirs: this confirms a new bot
    previous_endpoint = identity.endpoint_key
    invite.used_at = now
    invite.used_by_identity_id = identity.id
    identity.person_id = person.id
    identity.trust_level = int(Trust.LINKED)
    identity.endpoint_key = endpoint or invite.endpoint_key
    identity.last_seen_at = now  # confirmed just now: the silence clock starts again
    identity.verification = {"method": "invite_from_app", "verified_at": now.isoformat(), "invite_id": str(invite.id)}
    session.add(
        IdentityLinkEvent(
            identity_id=identity.id,
            person_id=person.id,
            action="link",
            method="invite_from_app",
            actor="identity_service",
            evidence={
                "invite_id": str(invite.id),
                "endpoint": identity.endpoint_key,
                "previous_endpoint": previous_endpoint,
            },
        )
    )
    audit(
        session, "identity", "endpoint_confirmed" if relink else "linked", identity_id=identity.id,
        person_id=person.id, method="invite_from_app", endpoint=identity.endpoint_key,
    )  # fmt: skip
    return person
