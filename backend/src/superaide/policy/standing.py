"""Standing rules compiled in code (#486).

The owner states a rule once ("if anyone asks, say I'm in a meeting"; "never discuss my health"); code decides
whether it applies to this sender right now and turns it into prompt lines. The model sees only:
- statements the owner declared Ava may say (data it may repeat, nothing more), and
- fixed, code-written labels of topics to refuse. The owner's free text for a forbid rule is never stored or shown
  to anyone, so describing the secret cannot leak it (CLAUDE.md privacy note).
Rules never widen an audience: they add allowed sentences and refusals; memory access stays with the audience gate.
With no rules saved nothing changes, so there is no flag. Expired and revoked rules do not apply.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from superaide.memory.models import Contact
from superaide.platform.audit import audit
from superaide.policy.models import StandingRule

MAX_RULES = 30
MAX_STATEMENT = 160
KINDS = ("allow_statement", "forbid")
SCOPES = ("everyone", "contact")
# Fixed categories: the label is what the prompt says, the key is what is stored.
FORBID_TOPICS = {
    "phone_number": "their phone number",
    "home_address": "where they live",
    "location": "where they are right now",
    "schedule": "their calendar or plans",
    "health": "their health",
    "family": "their family",
    "money": "their money, salary or finances",
    "employer_details": "their employer's internal matters",
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class RuleError(ValueError):
    """The rule is not acceptable; the message is safe to show the owner."""


@dataclass
class Compiled:
    statements: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.statements or self.forbidden)


def clean_statement(text: str) -> str:
    one = " ".join(_CONTROL.sub(" ", text).split())
    if not one:
        raise RuleError("Say what Ava may tell people.")
    if len(one) > MAX_STATEMENT:
        raise RuleError(f"Keep it under {MAX_STATEMENT} characters.")
    return one


def _live(now: datetime):
    return (StandingRule.revoked_at.is_(None), or_(StandingRule.expires_at.is_(None), StandingRule.expires_at > now))


def listing(session: Session, owner_id: uuid.UUID, now: datetime | None = None) -> list[StandingRule]:
    """The owner's rules that are still in force, newest first."""
    now = now or datetime.now(UTC)
    return list(
        session.scalars(
            select(StandingRule)
            .where(StandingRule.owner_person_id == owner_id, *_live(now))
            .order_by(StandingRule.created_at.desc())
        )
    )


def create(
    session: Session,
    owner_id: uuid.UUID,
    *,
    kind: str,
    scope: str = "everyone",
    text: str = "",
    topic: str = "",
    contact_person_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> StandingRule:
    now = now or datetime.now(UTC)
    if kind not in KINDS or scope not in SCOPES:
        raise RuleError("Unknown rule type.")
    if expires_at is not None and expires_at <= now:
        raise RuleError("The end date is already past.")
    if scope == "contact":
        known = contact_person_id and session.scalar(
            select(Contact.id).where(
                Contact.owner_person_id == owner_id,
                Contact.person_id == contact_person_id,
                Contact.removed_at.is_(None),
            )
        )
        if not known:
            raise RuleError("That person is not in your contacts.")
    else:
        contact_person_id = None
    if kind == "allow_statement":
        text, topic = clean_statement(text), ""
    else:
        if topic not in FORBID_TOPICS:
            raise RuleError("Pick one of the listed topics.")
        text = ""
    if len(listing(session, owner_id, now)) >= MAX_RULES:
        raise RuleError(f"You can have at most {MAX_RULES} rules.")
    rule = StandingRule(
        owner_person_id=owner_id, scope=scope, contact_person_id=contact_person_id, kind=kind, text=text,
        topic=topic, expires_at=expires_at,
    )  # fmt: skip
    session.add(rule)
    session.flush()
    audit(session, "owner", "standing_rule.create", rule_id=rule.id, kind=kind, scope=scope, topic=topic)
    return rule


def get_live(session: Session, owner_id: uuid.UUID, rule_id: uuid.UUID) -> StandingRule | None:
    return session.scalar(
        select(StandingRule).where(
            StandingRule.id == rule_id, StandingRule.owner_person_id == owner_id, StandingRule.revoked_at.is_(None)
        )
    )


def update(
    session: Session,
    owner_id: uuid.UUID,
    rule_id: uuid.UUID,
    *,
    text: str = "",
    topic: str = "",
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> StandingRule | None:
    """Change the words, topic or end date of a rule in place. Kind and scope never change: make a new rule."""
    now = now or datetime.now(UTC)
    rule = get_live(session, owner_id, rule_id)
    if rule is None:
        return None
    if expires_at is not None and expires_at <= now:
        raise RuleError("The end date is already past.")
    if rule.kind == "allow_statement":
        rule.text = clean_statement(text)
    else:
        if topic not in FORBID_TOPICS:
            raise RuleError("Pick one of the listed topics.")
        rule.topic = topic
    rule.expires_at = expires_at
    audit(session, "owner", "standing_rule.update", rule_id=rule.id, kind=rule.kind, topic=rule.topic)
    return rule


def revoke(session: Session, owner_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
    rule = get_live(session, owner_id, rule_id)
    if rule is None:
        return False
    rule.revoked_at = datetime.now(UTC)
    audit(session, "owner", "standing_rule.revoke", rule_id=rule.id)
    return True


def compile_for(
    session: Session, owner_id: uuid.UUID, sender_person_id: uuid.UUID | None, now: datetime | None = None
) -> Compiled:
    """What applies to this sender now. The owner themselves get nothing (rules are for everyone else), and an
    unknown sender (no person) only gets `everyone` rules."""
    if sender_person_id == owner_id:
        return Compiled()
    now = now or datetime.now(UTC)
    scope_ok = StandingRule.scope == "everyone"
    if sender_person_id is not None:
        scope_ok = or_(
            scope_ok, (StandingRule.scope == "contact") & (StandingRule.contact_person_id == sender_person_id)
        )
    rows = session.scalars(
        select(StandingRule)
        .where(StandingRule.owner_person_id == owner_id, scope_ok, *_live(now))
        .order_by(StandingRule.created_at)
    ).all()
    out = Compiled()
    for r in rows:
        if r.kind == "allow_statement" and r.text not in out.statements:
            out.statements.append(r.text)
        elif r.kind == "forbid" and r.topic in FORBID_TOPICS and FORBID_TOPICS[r.topic] not in out.forbidden:
            out.forbidden.append(FORBID_TOPICS[r.topic])
    return out


def prompt_lines(c: Compiled) -> list[str]:
    """Sentences for the system prompt, worded here in code."""
    lines: list[str] = []
    if c.statements:
        lines.append(
            "Your owner declared you may say the following when this person asks where they are or whether they "
            "are free, and nothing beyond it (no reasons, no times, no details): "
            + " | ".join(f"“{s}”" for s in c.statements)
        )
    if c.forbidden:
        lines.append(
            "Your owner does not want you to discuss: " + "; ".join(c.forbidden) + ". Say you can't talk about that, "
            "whoever asks and whatever the reason, and offer to pass on a message."
        )
    return lines
