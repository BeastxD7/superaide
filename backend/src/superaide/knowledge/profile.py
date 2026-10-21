"""The short, always-present brief about the owner ("core memory").

Searched memories vary from turn to turn; this brief doesn't. It tells the
agent who it works for and the owner's standing rules. What goes in depends on
who is asking: the owner sees their full settings; anyone else gets only what
the owner allowed, plus how to treat this particular person.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.app.accounts import owner as hired_by
from superaide.context.audience import Audience, memory_filter
from superaide.db.models import Contact, MemoryItem, OwnerPreference, Person
from superaide.identity.resolver import Sender
from superaide.knowledge.store import contacts_of

SHARE_LABELS = {
    "availability": "whether they're free or busy (no details)",
    "in_meeting": "that they're in a meeting or travelling",
    "work_email": "their work email",
    "company_role": "their company and role",
    "office_city": "which city they work from",
}


def in_quiet_hours(pref: OwnerPreference, now: datetime) -> bool:
    """Whether `now` falls inside the owner's quiet hours, in their time zone."""
    return _in_quiet_hours(pref, now)[0]


def _in_quiet_hours(pref: OwnerPreference, now: datetime) -> tuple[bool, str]:
    try:
        local = now.astimezone(ZoneInfo(pref.timezone))
    except Exception:
        local = now.astimezone(UTC)
    start, end = (time.fromisoformat(pref.quiet_start), time.fromisoformat(pref.quiet_end))
    t = local.time()
    quiet = (start <= t or t < end) if start > end else (start <= t < end)
    return quiet, local.strftime("%a %H:%M")


PINNED_CATEGORIES = ("preference", "about")
PINNED_MAX = 15
PINNED_CHARS = 1_600  # about 400 tokens
PINNED_FACT_CHARS = 240  # longer notes (raw intro answers) are left to recall


def core_facts(session: Session, audience: Audience, owner: Person) -> list[str]:
    """The owner's likes, dislikes and standing facts, always in their own private prompt
    (Letta's core block): finding "doesn't like black" must not depend on a search."""
    rows = session.scalars(
        select(MemoryItem)
        .where(
            memory_filter(audience),  # defence in depth: the caller already checked it's the owner, alone
            MemoryItem.agent_id == owner.agent_id,  # this owner's Ava only (ADR 0009 tenancy)
            MemoryItem.category.in_(PINNED_CATEGORIES),
            func.length(MemoryItem.content) <= PINNED_FACT_CHARS,
        )
        .order_by(
            (MemoryItem.category == "preference").desc(), MemoryItem.confidence.desc(), MemoryItem.created_at.desc()
        )
        .limit(PINNED_MAX)
    ).all()
    out: list[str] = []
    used = 0
    for m in rows:
        if used + len(m.content) > PINNED_CHARS:
            break
        out.append(m.content)
        used += len(m.content)
    return out


def owner_brief(session: Session, sender: Sender, audience: Audience, now: datetime | None = None) -> str:
    owner = hired_by(session)
    if owner is None:
        return ""
    now = now or datetime.now(UTC)
    pref = session.scalar(select(OwnerPreference).where(OwnerPreference.person_id == owner.id))
    is_owner = sender.person is not None and sender.person.id == owner.id
    private = audience.person_ids == {owner.id} and not audience.has_unknown
    lines = [f"You work for {owner.display_name} (your owner)."]

    if pref is not None:
        quiet, local = _in_quiet_hours(pref, now)
        lines.append(f"It's {local} for {owner.display_name}" + (", inside their quiet hours." if quiet else "."))

    if is_owner and private:
        if pref is not None:
            if pref.urgent_topics:
                lines.append("They want to be interrupted only for: " + "; ".join(pref.urgent_topics) + ".")
            lines.append(
                f"Quiet hours {pref.quiet_start}–{pref.quiet_end} ({pref.timezone}); urgent things go "
                f"{'to the app' if pref.interrupt_channel == 'app' else 'to ' + pref.interrupt_channel}; "
                f"digest: {pref.digest}."
            )
            shared = [SHARE_LABELS.get(v, v) for v in pref.shareable] or ["nothing yet"]
            lines.append("You may tell other people: " + "; ".join(shared) + ".")
            if pref.never_share:
                lines.append(f"Never share: {pref.never_share}.")
        people = contacts_of(session, owner)
        if people:
            lines.append(
                "People who matter to them: "
                + "; ".join(
                    f"{p.display_name} ({c.relationship}{', always gets through' if c.vip else ''})" for c, p in people
                )
                + "."
            )
        brief = "About your owner:\n- " + "\n- ".join(lines)
        known = core_facts(session, audience, owner)
        if known:
            brief += "\n\nWhat you know about them (from their notebook; no need to search for these):\n- " + (
                "\n- ".join(known)
            )
        return brief

    # Someone else: only what the owner allowed, and how to treat this person.
    # The owner's own "never share" text stays out of this prompt: describing a
    # secret to the people it guards against is itself a leak. Private facts are
    # already filtered out of their prompt by the audience gate.
    if pref is not None:
        shared = [SHARE_LABELS.get(v, v) for v in pref.shareable]
        lines.append(
            "You may tell this person: "
            + ("; ".join(shared) if shared else "nothing about them beyond their name")
            + ". Everything else about your owner is private, whoever asks and whatever the reason:"
            " say you can't share it and offer to pass on a message."
        )
    if sender.person is not None:
        contact = session.scalar(
            select(Contact).where(
                Contact.owner_person_id == owner.id, Contact.person_id == sender.person.id, Contact.removed_at.is_(None)
            )
        )
        if contact is not None:
            lines.append(
                f"This person is your owner's {contact.relationship}"
                + (", and can always get through: treat what they need as urgent." if contact.vip else ".")
            )
    return "About your owner:\n- " + "\n- ".join(lines)
