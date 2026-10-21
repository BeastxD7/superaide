"""The short, always-present brief about the owner ("core memory").

Searched memories vary from turn to turn; this brief doesn't. It tells the
agent who it works for and the owner's standing rules. What goes in depends on
who is asking: the owner sees their full settings; anyone else gets only what
the owner allowed, plus how to treat this particular person.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app.accounts import owner as hired_by
from superaide.context.audience import Audience
from superaide.db.models import Contact, OwnerPreference
from superaide.identity.resolver import Sender
from superaide.knowledge.store import contacts_of

SHARE_LABELS = {
    "availability": "whether they're free or busy (no details)",
    "in_meeting": "that they're in a meeting or travelling",
    "work_email": "their work email",
    "company_role": "their company and role",
    "office_city": "which city they work from",
}


def _in_quiet_hours(pref: OwnerPreference, now: datetime) -> tuple[bool, str]:
    try:
        local = now.astimezone(ZoneInfo(pref.timezone))
    except Exception:
        local = now.astimezone(UTC)
    start, end = (time.fromisoformat(pref.quiet_start), time.fromisoformat(pref.quiet_end))
    t = local.time()
    quiet = (start <= t or t < end) if start > end else (start <= t < end)
    return quiet, local.strftime("%a %H:%M")


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
        return "About your owner:\n- " + "\n- ".join(lines)

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
