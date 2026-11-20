"""Friends and known contacts (#479, #504): who is acknowledged, who is trusted, decided in code from the database.

Four tiers, read by `tier_of` from the sender the resolver produced and the database, never from the message:

- stranger: not linked to anyone, not listed by the owner.
- unverified friend: an account the owner listed for a contact (by username) or that opened an invite the owner
  made. It is acknowledged, but it stays UNLINKED, so the audience gate and the model treat it as a stranger:
  public details only, nothing personal or about work, and no name. The owner sees the platform's immutable
  identifier in the app and approves it (or declines).
- verified friend: linked to a contact of the owner. Greeted by name, in code.
- colleague: linked, with the colleague relation.

The model never writes any of this. A display name, a username that is not listed, or "I'm Meera" in the text
changes nothing: the only inputs are the platform's immutable id, the handles the owner typed in the app, and the
owner's approval.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.channels.base import InboundMessage
from superaide.identity.models import (
    ChannelIdentity,
    ChannelInvite,
    ContactHandle,
    FriendCandidate,
    IdentityLinkEvent,
    Person,
)
from superaide.identity.resolver import Sender
from superaide.identity.trust import Trust
from superaide.memory.models import Contact, Context, Event
from superaide.platform.audit import audit

PROVIDERS = ("telegram", "slack")
INVITE_TTL = timedelta(days=7)
USERNAME_RE = re.compile(r"^[a-z0-9_]{4,32}$")
SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,20}$")
# Fixed words (code, never the model). The marks let acknowledge() say it once a day.
ACK_MARK = "I can't confirm which account this is yet"
ACK = (
    f"Good to hear from you. {ACK_MARK}, so until they confirm it I can only share what's public. "
    "I'll let them know you wrote."
)
INVITE_OK = (
    "Thanks, I've noted that you opened the link. I'll let them know, and once they confirm this account I'll "
    "recognise you here."
)
INVITE_REFUSED = "That link has expired or was already used. Ask for a new one."


class Tier(StrEnum):
    STRANGER = "stranger"
    UNVERIFIED_FRIEND = "unverified_friend"
    VERIFIED_FRIEND = "verified_friend"
    COLLEAGUE = "colleague"


def tier_of(session: Session, sender: Sender) -> Tier:
    """The sender's tier. Takes the resolved sender and the database only: nothing the message said can matter."""
    person = sender.person
    if person is not None and not sender.unconfirmed:
        if person.relation in ("manager", "colleague"):
            return Tier.COLLEAGUE
        if _contact_of(session, person) is not None:
            return Tier.VERIFIED_FRIEND
        return Tier.STRANGER  # linked, but not on the owner's list: no standing as a friend
    cand = session.scalar(
        select(FriendCandidate).where(
            FriendCandidate.identity_id == sender.identity.id, FriendCandidate.state == "pending"
        )
    )
    return Tier.UNVERIFIED_FRIEND if cand is not None and person is None else Tier.STRANGER


def _contact_of(session: Session, person: Person) -> Contact | None:
    return session.scalar(select(Contact).where(Contact.person_id == person.id, Contact.removed_at.is_(None)))


def normalise_handle(provider: str, raw: str) -> tuple[str, str]:
    """(kind, value) for what the owner typed, or ValueError. A Telegram username is lower-cased, an id is digits."""
    text = " ".join(str(raw or "").split())
    if provider == "telegram":
        text = re.sub(r"^(https?://)?(www\.)?t\.me/", "", text, flags=re.I).lstrip("@").strip().lower()
        if text.isdigit() and 1 <= len(text) <= 20:
            return "id", text
        if USERNAME_RE.match(text):
            return "username", text
        raise ValueError("Enter a Telegram username (like @meera_i) or the numeric Telegram id.")
    if provider == "slack":
        text = text.upper()
        if SLACK_ID_RE.match(text):
            return "id", text
        raise ValueError("Enter the Slack member id (it starts with U or W). A display name can be copied by anyone.")
    raise ValueError("Unsupported channel.")


def add_handle(session: Session, contact: Contact, provider: str, raw: str) -> ContactHandle:
    kind, value = normalise_handle(provider, raw)
    taken = session.scalar(
        select(ContactHandle).where(
            ContactHandle.provider == provider, ContactHandle.kind == kind, ContactHandle.value == value
        )
    )
    if taken is not None:
        if taken.contact_id == contact.id:
            return taken
        raise ValueError("That account is already listed for someone else.")
    row = ContactHandle(contact_id=contact.id, provider=provider, kind=kind, value=value)
    session.add(row)
    session.flush()
    audit(session, "app", "contact_handle_added", contact_id=contact.id, provider=provider, kind=kind)
    return row


def _tell_owner(session: Session, contact: Contact, how: str) -> None:
    """Fixed words with the owner's own name for the contact. The username and the platform id go only in the app
    card: this text lands in the chat the owner's model reads, and those strings are chosen by the sender."""
    from superaide.app.accounts import owner
    from superaide.proactive.notify import notify_owner

    boss = owner(session)
    person = session.get(Person, contact.person_id)
    if boss is None or person is None:
        return
    notify_owner(
        session,
        boss,
        f"An account {how} {person.display_name} wrote to Ava. I can't tell if it is really them. Open People to "
        "see the account and approve it. Until you do, Ava shares only what is public.",
        {"kind": "friend_pending", "contact_id": str(contact.id)},
        channel="default",
    )


def _candidate(session: Session, identity: ChannelIdentity, contact: Contact, via: str) -> FriendCandidate | None:
    """Open a pending candidate once. A declined account stays declined; None when nothing new was opened."""
    row = session.scalar(select(FriendCandidate).where(FriendCandidate.identity_id == identity.id))
    if row is not None:
        return None
    row = FriendCandidate(identity_id=identity.id, contact_id=contact.id, via=via, state="pending")
    session.add(row)
    session.flush()
    audit(session, "identity", "friend_pending", identity_id=identity.id, contact_id=contact.id, via=via)
    _tell_owner(session, contact, "listed as" if via == "handle" else "that opened the invite for")
    return row


def observe(session: Session, sender: Sender, msg: InboundMessage) -> None:
    """For an unlinked sender: does the owner list this account for a contact? By the platform's immutable id, or by a
    username the owner typed. The display name and the message text are never read."""
    identity = sender.identity
    if sender.person is not None or "external" in (identity.risk_flags or []) or msg.provider not in PROVIDERS:
        return
    wanted = [("id", identity.stable_key)]
    if msg.provider == "telegram" and msg.sender_username:
        wanted.append(("username", msg.sender_username.lstrip("@").lower()))
    for kind, value in wanted:
        handle = session.scalar(
            select(ContactHandle).where(
                ContactHandle.provider == msg.provider, ContactHandle.kind == kind, ContactHandle.value == value
            )
        )
        contact = session.get(Contact, handle.contact_id) if handle is not None else None
        if contact is None or contact.removed_at is not None:
            continue
        if kind == "id":
            # The owner typed this immutable id for this contact: that is the owner's own approval.
            person = session.get(Person, contact.person_id)
            if person is not None and identity.person_id is None:
                _link(session, identity, person, method="owner_listed_id", actor="owner")
                sender.person = person  # this very message is already from a verified friend
                sender.trust = int(Trust.LINKED)
            return
        _candidate(session, identity, contact, "handle")
        return


def _link(session: Session, identity: ChannelIdentity, person: Person, *, method: str, actor: str) -> None:
    now = datetime.now(UTC)
    identity.person_id = person.id
    identity.trust_level = int(Trust.LINKED)
    identity.last_seen_at = now
    identity.verification = {"method": method, "verified_at": now.isoformat()}
    session.add(
        IdentityLinkEvent(identity_id=identity.id, person_id=person.id, action="link", method=method, actor=actor)
    )
    audit(session, actor, "friend_linked", identity_id=identity.id, person_id=person.id, method=method)


def pending_for(session: Session, owner: Person) -> list[dict[str, Any]]:
    """What the owner is asked: the account's immutable identifier first. The username and the display name are
    shown labelled as changeable, because the sender chose them."""
    out: list[dict[str, Any]] = []
    rows = session.execute(
        select(FriendCandidate, ChannelIdentity, Contact)
        .join(ChannelIdentity, ChannelIdentity.id == FriendCandidate.identity_id)
        .join(Contact, Contact.id == FriendCandidate.contact_id)
        .where(
            FriendCandidate.state == "pending",
            Contact.owner_person_id == owner.id,
            Contact.removed_at.is_(None),
            ChannelIdentity.person_id.is_(None),
        )
        .order_by(FriendCandidate.created_at, FriendCandidate.id)
    )
    for cand, ident, contact in rows:
        person = session.get(Person, contact.person_id)
        out.append(
            {
                "identity_id": str(ident.id),
                "provider": ident.provider,
                "platform_id": ident.stable_key,
                "username": str((ident.aux_handles or {}).get("username") or ""),
                "display_name": str((ident.aux_handles or {}).get("display_name") or ""),
                "via": cand.via,
                "contact": {"id": str(contact.id), "name": person.display_name if person else ""},
                "first_seen": cand.created_at.isoformat(),
            }
        )
    return out


class FriendError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _pending_row(
    session: Session, owner: Person, identity_id: Any, contact_id: Any = None
) -> tuple[FriendCandidate, Contact]:
    cand = session.scalar(select(FriendCandidate).where(FriendCandidate.identity_id == identity_id))
    contact = session.get(Contact, cand.contact_id) if cand is not None else None
    if (
        cand is None
        or cand.state != "pending"
        or contact is None
        or contact.owner_person_id != owner.id
        or contact.removed_at is not None
        or (contact_id is not None and contact.id != contact_id)
    ):
        raise FriendError(404, "Not found")
    return cand, contact


def approve(session: Session, owner: Person, identity_id: Any) -> None:
    """The owner says: this account is this contact. The only path from unverified to verified."""
    cand, contact = _pending_row(session, owner, identity_id)
    identity = session.get(ChannelIdentity, identity_id)
    person = session.get(Person, contact.person_id)
    if identity is None or person is None:
        raise FriendError(404, "Not found")
    if identity.person_id is not None:
        raise FriendError(409, "That account is already linked to someone.")
    if "external" in (identity.risk_flags or []) or identity.lifecycle != "active":
        raise FriendError(409, "That account cannot be linked.")
    _link(session, identity, person, method="owner_approved_friend", actor=owner.display_name)
    session.delete(cand)
    session.flush()


def decline(session: Session, owner: Person, identity_id: Any) -> None:
    cand, _ = _pending_row(session, owner, identity_id)
    cand.state, cand.decided_at = "declined", datetime.now(UTC)
    audit(session, "app", "friend_declined", identity_id=identity_id)


# --- invites (Telegram deep link) -----------------------------------------------------------------------------


def create_invite(session: Session, contact: Contact, *, endpoint: str) -> str:
    """A one-time link for this contact, valid a week, only through this bot. Hashed at rest, like every invite."""
    from superaide.app.accounts import _hash

    code = secrets.token_urlsafe(12)
    session.add(
        ChannelInvite(
            person_id=contact.person_id,
            provider="telegram",
            code_hash=_hash(code),
            expires_at=datetime.now(UTC) + INVITE_TTL,
            endpoint_key=endpoint,
            purpose="friend",
        )
    )
    audit(session, "app", "friend_invite_created", contact_id=contact.id)
    return code


def redeem_invite(session: Session, *, code: str, identity: ChannelIdentity, endpoint: str) -> str | None:
    """None when this is not a friend invite (the ordinary link flow goes on). Otherwise the fixed reply: the account
    becomes an unverified friend of that contact, never a linked person, and the owner is asked."""
    from superaide.app.accounts import _hash

    invite = session.scalar(
        select(ChannelInvite).where(
            ChannelInvite.code_hash == _hash(code),
            ChannelInvite.provider == "telegram",
            ChannelInvite.purpose == "friend",
        )
    )
    if invite is None:
        return None
    now = datetime.now(UTC)
    contact = session.scalar(select(Contact).where(Contact.person_id == invite.person_id, Contact.removed_at.is_(None)))
    if (
        contact is None
        or invite.used_at is not None
        or invite.expires_at <= now
        or (invite.endpoint_key and invite.endpoint_key != endpoint)
        or identity.person_id is not None
        or "external" in (identity.risk_flags or [])
    ):
        audit(session, "identity", "invite_rejected", provider="telegram", reason="friend_invite", invite_id=invite.id)
        return INVITE_REFUSED
    invite.used_at, invite.used_by_identity_id = now, identity.id
    existing = session.scalar(select(FriendCandidate).where(FriendCandidate.identity_id == identity.id))
    if existing is not None and existing.state == "declined":
        existing.state, existing.contact_id, existing.via, existing.decided_at = "pending", contact.id, "invite", None
        _tell_owner(session, contact, "that opened the invite for")
    else:
        _candidate(session, identity, contact, "invite")
    return INVITE_OK


# --- what the sender is told ------------------------------------------------------------------------------------


def _said(session: Session, ctx: Context, mark: str, since: timedelta) -> bool:
    return (
        session.scalar(
            select(Event.id)
            .where(
                Event.context_id == ctx.id,
                Event.direction == "out",
                Event.content.contains(mark),
                Event.created_at >= datetime.now(UTC) - since,
            )
            .limit(1)
        )
        is not None
    )


def acknowledge(session: Session, ctx: Context, sender: Sender, reply: str) -> str:
    """An unverified friend gets one fixed line (once a day) after the ordinary receptionist reply. No name."""
    if tier_of(session, sender) is not Tier.UNVERIFIED_FRIEND or _said(session, ctx, ACK_MARK, timedelta(days=1)):
        return reply
    return f"{reply}\n\n{ACK}" if reply else ACK


def greet(session: Session, ctx: Context, sender: Sender, reply: str) -> str:
    """A verified friend is greeted by the owner's own name for them, once, on the first reply in the conversation.
    Never the platform display name, never the model."""
    if sender.person is None or tier_of(session, sender) is not Tier.VERIFIED_FRIEND:
        return reply
    since = datetime.now(UTC)
    try:  # the first reply since the owner approved this account (earlier replies were to a stranger)
        since = datetime.fromisoformat(str((sender.identity.verification or {}).get("verified_at")))
    except ValueError:
        pass
    if (
        session.scalar(
            select(Event.id)
            .where(Event.context_id == ctx.id, Event.direction == "out", Event.created_at >= since)
            .limit(1)
        )
        is not None
    ):
        return reply
    first = (sender.person.display_name.split() or [""])[0]
    return f"Hi {first}! {reply}" if first else reply
