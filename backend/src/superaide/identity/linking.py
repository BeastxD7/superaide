"""Linking a new channel to a person.

The only way a new account becomes "you" is a one-time code sent to a channel
the agent already trusts, typed back into the new channel. That proves the
same human controls both. Codes are 6 digits, single use, expire after 10
minutes and lock after 5 wrong tries. Links are append-only events and can be
undone.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.channels.base import Messenger
from superaide.channels.health import DeliveryFailed
from superaide.config import Settings
from superaide.db.models import ChannelIdentity, IdentityLinkEvent, LinkChallenge, Person
from superaide.identity.trust import Trust

CODE_RE = re.compile(r"^\s*(\d{6})\s*$")
CLAIM_RE = re.compile(
    r"\b(?:i am|i'm|im|it's|its|it is|this is)\s+([a-z][a-z\-']{1,40}(?:\s+[a-z][a-z\-']{1,40})?)",
    re.IGNORECASE,
)


def detect_code(text: str) -> str | None:
    m = CODE_RE.match(text)
    return m.group(1) if m else None


def detect_claimed_name(text: str) -> str | None:
    m = CLAIM_RE.search(text)
    return m.group(1).strip() if m else None


def _hash(salt: str, code: str) -> str:
    return hashlib.sha256((salt + code).encode()).hexdigest()


def trusted_identities(session: Session, person: Person) -> list[ChannelIdentity]:
    rows = session.scalars(
        select(ChannelIdentity)
        .where(
            ChannelIdentity.person_id == person.id,
            ChannelIdentity.lifecycle == "active",
            ChannelIdentity.trust_level >= int(Trust.LINKED),
        )
        .order_by(ChannelIdentity.trust_level.desc(), ChannelIdentity.last_seen_at.desc().nulls_last())
    ).all()
    return [r for r in rows if "external" not in (r.risk_flags or [])]


def find_person_by_name(session: Session, name: str) -> Person | None:
    """Match a claimed name to a person who has at least one trusted channel.

    Matches the full display name or the first name, case-insensitively. The
    result is only used to decide where to send a code, never to grant access.
    """
    wanted = name.casefold().strip()
    if not wanted:
        return None
    people = session.scalars(select(Person).where(Person.status == "active")).all()
    for p in people:
        full = p.display_name.casefold().strip()
        if not full:
            continue
        if wanted == full or wanted == full.split()[0] or wanted.split()[0] == full.split()[0]:
            if trusted_identities(session, p):
                return p
    return None


def start_challenge(
    session: Session,
    *,
    new_identity: ChannelIdentity,
    person: Person,
    messengers: dict[str, Messenger],
    settings: Settings,
    agent_name: str,
) -> LinkChallenge | None:
    now = datetime.now(UTC)
    if new_identity.person_id == person.id:
        return None
    if "external" in (new_identity.risk_flags or []):
        audit(session, "identity", "link_refused", reason="external_account", identity_id=new_identity.id)
        return None
    recent = session.scalar(
        select(func.count())
        .select_from(LinkChallenge)
        .where(LinkChallenge.identity_id == new_identity.id, LinkChallenge.created_at > now - timedelta(days=1))
    )
    if (recent or 0) >= settings.link_challenges_per_identity_per_day:
        audit(session, "identity", "link_rate_limited", identity_id=new_identity.id)
        return None
    candidates = [
        i
        for i in trusted_identities(session, person)
        if (i.provider, i.tenant_scope, i.stable_key)
        != (new_identity.provider, new_identity.tenant_scope, new_identity.stable_key)
    ]
    via = next((i for i in candidates if i.provider in messengers), None)
    if via is None:
        audit(session, "identity", "link_no_trusted_channel", person_id=person.id)
        return None

    code = f"{secrets.randbelow(10**6):06d}"
    salt = secrets.token_hex(16)
    challenge = LinkChallenge(
        identity_id=new_identity.id,
        person_id=person.id,
        via_identity_id=via.id,
        code_hash=_hash(salt, code),
        salt=salt,
        expires_at=now + timedelta(minutes=settings.link_code_ttl_minutes),
    )
    session.add(challenge)
    session.flush()
    handle = new_identity.aux_handles.get("username") or new_identity.aux_handles.get("display_name") or "an account"
    try:
        messengers[via.provider].send_dm(
            via,
            f"{agent_name} here. Someone on {new_identity.provider.capitalize()} ({handle}) just said they are you. "
            f"If that was you, type this code in that chat within {settings.link_code_ttl_minutes} minutes: {code}\n"
            "If it wasn't you, ignore this. They get nothing without the code.",
        )
    except DeliveryFailed as exc:  # the code expires unused; the channel's health says why
        audit(session, "identity", "link_code_not_delivered", provider=via.provider, reason=exc.kind)
    audit(
        session,
        "identity",
        "link_challenge_sent",
        challenge_id=challenge.id,
        identity_id=new_identity.id,
        person_id=person.id,
        via_identity_id=via.id,
    )
    return challenge


@dataclass
class VerifyResult:
    ok: bool
    reason: str  # linked | no_challenge | expired | locked | wrong_code
    person: Person | None = None


def verify_code(
    session: Session,
    *,
    identity: ChannelIdentity,
    code: str,
    messengers: dict[str, Messenger],
    settings: Settings,
    agent_name: str,
) -> VerifyResult:
    now = datetime.now(UTC)
    challenge = session.scalar(
        select(LinkChallenge)
        .where(LinkChallenge.identity_id == identity.id, LinkChallenge.status == "pending")
        .order_by(LinkChallenge.created_at.desc())
    )
    if challenge is None:
        return VerifyResult(False, "no_challenge")
    if challenge.expires_at <= now:
        challenge.status = "expired"
        audit(session, "identity", "link_code_expired", challenge_id=challenge.id)
        return VerifyResult(False, "expired")
    challenge.attempts += 1
    if not hmac.compare_digest(_hash(challenge.salt, code), challenge.code_hash):
        if challenge.attempts >= settings.link_code_max_attempts:
            challenge.status = "locked"
            audit(session, "identity", "link_code_locked", challenge_id=challenge.id)
            return VerifyResult(False, "locked")
        audit(session, "identity", "link_code_wrong", challenge_id=challenge.id, attempts=challenge.attempts)
        return VerifyResult(False, "wrong_code")

    person = session.get(Person, challenge.person_id)
    assert person is not None
    challenge.status = "verified"
    identity.person_id = person.id
    identity.trust_level = int(Trust.LINKED)
    identity.verification = {
        "method": "code_via_trusted_channel",
        "verified_at": now.isoformat(),
        "via_identity_id": str(challenge.via_identity_id),
        "challenge_id": str(challenge.id),
    }
    session.add(
        IdentityLinkEvent(
            identity_id=identity.id,
            person_id=person.id,
            action="link",
            method="code_via_trusted_channel",
            actor="identity_service",
            evidence={"challenge_id": str(challenge.id), "via_identity_id": str(challenge.via_identity_id)},
        )
    )
    audit(session, "identity", "linked", identity_id=identity.id, person_id=person.id)
    for trusted in trusted_identities(session, person):
        if trusted.id != identity.id and trusted.provider in messengers:
            try:
                messengers[trusted.provider].send_dm(
                    trusted,
                    f"{agent_name} here. I linked a {identity.provider} account to you. "
                    "If that wasn't you, unlink it from the dashboard (People page) or run: "
                    f"superaide unlink --identity {identity.id}",
                )
            except DeliveryFailed as exc:
                audit(session, "identity", "link_notice_not_delivered", provider=trusted.provider, reason=exc.kind)
    return VerifyResult(True, "linked", person)


def unlink(session: Session, identity: ChannelIdentity, *, actor: str, reason: str = "") -> None:
    previous = identity.person_id
    identity.person_id = None
    identity.trust_level = int(Trust.AUTHENTICATED)
    identity.verification = {"method": "unlinked", "at": datetime.now(UTC).isoformat()}
    session.add(
        IdentityLinkEvent(
            identity_id=identity.id,
            person_id=previous,
            action="unlink",
            method="manual",
            actor=actor,
            evidence={"reason": reason},
        )
    )
    audit(session, actor, "unlinked", identity_id=identity.id, previous_person_id=previous)


def operator_link(session: Session, identity: ChannelIdentity, person: Person, *, actor: str) -> None:
    """You, at your own dashboard or terminal, say "this account is this person".
    Same standing as add-manager: the operator is trusted, a message never is."""
    if "external" in (identity.risk_flags or []):
        raise ValueError("Accounts from another company cannot be linked")
    identity.person_id = person.id
    identity.trust_level = int(Trust.LINKED)
    identity.verification = {"method": "operator_dashboard", "verified_at": datetime.now(UTC).isoformat()}
    session.add(
        IdentityLinkEvent(
            identity_id=identity.id, person_id=person.id, action="link", method="operator_dashboard", actor=actor
        )
    )
    audit(session, actor, "linked_by_operator", identity_id=identity.id, person_id=person.id)


def bootstrap_person(
    session: Session,
    *,
    name: str,
    relation: str,
    identities: list[tuple[str, str, str]],
    actor: str = "operator",
) -> Person:
    """Create a person and their first channels. Run by you at the terminal, so
    these channels start at the LINKED tier with method 'operator_bootstrap'."""
    person = Person(display_name=name, relation=relation)
    session.add(person)
    session.flush()
    for provider, tenant_scope, stable_key in identities:
        from superaide.identity.resolver import find_identity

        identity = find_identity(session, provider, tenant_scope, stable_key)
        if identity is None:
            identity = ChannelIdentity(
                provider=provider, tenant_scope=tenant_scope, stable_key=stable_key, aux_handles={}
            )
            session.add(identity)
            session.flush()
        identity.person_id = person.id
        identity.trust_level = int(Trust.LINKED)
        identity.verification = {"method": "operator_bootstrap", "verified_at": datetime.now(UTC).isoformat()}
        session.add(
            IdentityLinkEvent(
                identity_id=identity.id, person_id=person.id, action="link", method="operator_bootstrap", actor=actor
            )
        )
    audit(session, actor, "person_bootstrapped", person_id=person.id, relation=relation, channels=len(identities))
    return person
