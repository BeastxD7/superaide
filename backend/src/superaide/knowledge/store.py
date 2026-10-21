"""Writing what the assistant learns about its owner, with provenance.

Structured settings (people, urgency, quiet hours, sharing) live in typed tables
(Contact, OwnerPreference). Free-form knowledge lives in MemoryItem rows, each
tagged with a category, a source and, for files and links, the Document it came
from. Owner facts are private to the owner unless marked shareable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Contact, Document, MemoryItem, OwnerPreference, Person
from superaide.db.session import rows_affected

CATEGORIES = ("about", "work", "person", "preference", "urgency", "availability", "sharing", "other")
SOURCES = ("chat", "intro_chat", "intro_call", "document", "link", "telegram", "slack", "owner_edit")


def remember_fact(
    session: Session,
    *,
    owner: Person,
    content: str,
    category: str = "other",
    source: str = "chat",
    document: Document | None = None,
    confidence: float = 1.0,
    shareable: bool = False,
    origin_context_id: Any = None,
) -> MemoryItem | None:
    """Store one fact about the owner. Returns None if the same fact is already known."""
    content = " ".join(content.split())[:2000]
    if not content:
        return None
    category = category if category in CATEGORIES else "other"
    existing = session.scalar(
        select(MemoryItem.id).where(
            MemoryItem.content == content,
            MemoryItem.valid_to.is_(None),
            MemoryItem.subject_person_ids.contains([owner.id]),
        )
    )
    if existing is not None:
        return None
    item = MemoryItem(
        content=content,
        subject_person_ids=[owner.id],
        origin_context_id=origin_context_id,
        allowed_kind="public" if shareable else "persons",
        allowed_person_ids=[] if shareable else [owner.id],
        min_trust=0 if shareable else 2,
        category=category,
        source=source if source in SOURCES else "chat",
        document_id=document.id if document else None,
        confidence=max(0.0, min(1.0, confidence)),
    )
    session.add(item)
    session.flush()
    audit(session, "memory", "remembered", memory_id=item.id, source=item.source, category=item.category)
    return item


def retire_document(session: Session, document: Document) -> int:
    """Remove a shared file or link and every fact that came from it."""
    now = datetime.now(UTC)
    document.removed_at = now
    n = rows_affected(
        session.execute(
            update(MemoryItem)
            .where(MemoryItem.document_id == document.id, MemoryItem.valid_to.is_(None))
            .values(valid_to=now)
        )
    )
    audit(session, "memory", "document_removed", document_id=document.id, facts_retired=n)
    return n


def preferences_for(session: Session, person: Person) -> OwnerPreference:
    pref = session.scalar(select(OwnerPreference).where(OwnerPreference.person_id == person.id))
    if pref is None:
        pref = OwnerPreference(person_id=person.id)
        session.add(pref)
        session.flush()
    return pref


def add_contact(
    session: Session, *, owner: Person, name: str, relationship: str = "other", vip: bool = False, notes: str = ""
) -> Contact:
    """Someone who matters to the owner. They become a Person (relation 'contact', no
    authority), so their Telegram or Slack can be linked to them later."""
    name = " ".join(name.split())[:120]
    existing = session.scalar(
        select(Contact)
        .join(Person, Person.id == Contact.person_id)
        .where(
            Contact.owner_person_id == owner.id,
            Contact.removed_at.is_(None),
            # Case-insensitive equality, not ILIKE: in a name, % _ and \ are just characters.
            func.lower(Person.display_name) == name.lower(),
        )
    )
    if existing is not None:
        existing.relationship = relationship or existing.relationship
        existing.vip = existing.vip or vip
        if notes:
            existing.notes = notes[:1000]
        return existing
    person = Person(display_name=name, relation="contact")
    session.add(person)
    session.flush()
    contact = Contact(
        owner_person_id=owner.id, person_id=person.id, relationship=relationship[:30], vip=vip, notes=notes[:1000]
    )
    session.add(contact)
    session.flush()
    audit(session, "people", "contact_added", contact_id=contact.id, vip=vip)
    return contact


def contacts_of(session: Session, owner: Person) -> list[tuple[Contact, Person]]:
    rows = session.execute(
        select(Contact, Person)
        .join(Person, Person.id == Contact.person_id)
        .where(Contact.owner_person_id == owner.id, Contact.removed_at.is_(None))
        .order_by(Contact.vip.desc(), Person.display_name)
    ).all()
    return [(c, p) for c, p in rows]
