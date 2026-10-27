"""Import: a passport goes in through the same rules as anything else Ava learns.

What the file cannot decide: who the facts are about (always the signed-in owner), who may see
them (always the owner alone, whatever the file says about sharing), how much Ava trusts them
(imported facts are capped at confidence 0.6 and carry source "imported"), and whether they
replace anything (they never do: a fact Ava already knows is skipped, nothing is overwritten).
Contacts come in without VIP status, which changes when Ava interrupts the owner, so that stays
the owner's own choice. Every row is tagged with its import so one tap erases the whole set.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from superaide.features.passport.schema import Passport
from superaide.identity.models import Person
from superaide.memory.knowledge.store import find_same_fact
from superaide.memory.models import Contact, Document, MemoryImport, MemoryItem
from superaide.memory.recall.lexicon import normalise
from superaide.platform.audit import audit
from superaide.platform.db.session import rows_affected


@dataclass
class Plan:
    new_facts: list[Any]
    duplicate_facts: int
    new_sources: list[Any]
    duplicate_sources: int
    new_people: list[Any]
    duplicate_people: int


def _digest(source: str, text: str) -> str:
    return hashlib.sha256((source + "\n" + text).encode()).hexdigest()  # the same digest ingest() uses


def plan(session: Session, owner: Person, passport: Passport) -> Plan:
    """What an import would add and what Ava already knows. Writes nothing."""
    seen: set[str] = set()
    new_facts, dup_facts = [], 0
    for f in passport.facts:
        norm = normalise(f.content)
        if norm in seen or find_same_fact(session, owner, f.content) is not None:
            dup_facts += 1
            continue
        seen.add(norm)
        new_facts.append(f)

    have = set(session.scalars(select(Document.sha256).where(Document.owner_person_id == owner.id)))
    new_sources, dup_sources = [], 0
    for src in passport.sources:
        digest = _digest(src.source, src.text)
        if digest in have:
            dup_sources += 1
            continue
        have.add(digest)
        new_sources.append(src)

    known = {
        n.lower()
        for n in session.scalars(
            select(Person.display_name)
            .join(Contact, Contact.person_id == Person.id)
            .where(Contact.owner_person_id == owner.id, Contact.removed_at.is_(None))
        )
    }
    new_people, dup_people = [], 0
    for p in passport.people:
        if p.name.lower() in known:
            dup_people += 1
            continue
        known.add(p.name.lower())
        new_people.append(p)
    return Plan(new_facts, dup_facts, new_sources, dup_sources, new_people, dup_people)


def preview(plan_: Plan, passport: Passport) -> dict[str, Any]:
    return {
        "assistant": passport.assistant,
        "schema_version": passport.schema_version,
        "facts": len(passport.facts),
        "new_facts": len(plan_.new_facts),
        "already_known_facts": plan_.duplicate_facts,
        "sources": len(passport.sources),
        "new_sources": len(plan_.new_sources),
        "people": len(passport.people),
        "new_people": len(plan_.new_people),
        "skipped": passport.skipped,
        "skipped_count": passport.skipped_count,
        "samples": [f.content[:160] for f in plan_.new_facts[:3]],
    }


def apply(session: Session, owner: Person, passport: Passport, *, label: str) -> MemoryImport:
    """Write the import. The caller is inside one transaction, so a failure leaves nothing behind."""
    from superaide.memory.recall import vectors

    p = plan(session, owner, passport)
    record = MemoryImport(
        owner_person_id=owner.id,
        label=label[:200],
        assistant=passport.assistant,
        schema_version=passport.schema_version,
        fact_count=len(p.new_facts),
        source_count=len(p.new_sources),
        people_count=len(p.new_people),
    )
    session.add(record)
    session.flush()

    doc_ids: dict[str, uuid.UUID] = {}
    for src in p.new_sources:
        doc = Document(
            owner_person_id=owner.id,
            kind=src.kind,
            title=src.title or "Imported file",
            source=src.source,
            sha256=_digest(src.source, src.text),
            size_bytes=len(src.text.encode()),
            text=src.text,
            status="stored",  # facts arrive with the passport; nothing is read by a model
            warnings=src.warnings,
            import_id=record.id,
        )
        session.add(doc)
        session.flush()
        doc_ids[src.key] = doc.id

    now = datetime.now(UTC)
    for f in p.new_facts:
        item = MemoryItem(
            content=f.content,
            subject_person_ids=[owner.id],
            allowed_kind="persons",  # never from the file: imported facts are the owner's alone
            allowed_person_ids=[owner.id],
            min_trust=2,
            category=f.category,
            source="imported",
            document_id=doc_ids.get(f.source_key or ""),
            confidence=f.confidence,
            valid_from=f.valid_from or now,
            import_id=record.id,
        )
        session.add(item)
        session.flush()
        vectors.schedule(session, item)

    for person in p.new_people:
        who = Person(display_name=person.name, relation="contact")  # a contact has no authority
        session.add(who)
        session.flush()
        session.add(
            Contact(
                owner_person_id=owner.id,
                person_id=who.id,
                relationship=person.relationship,
                vip=False,
                notes=person.notes,
                import_id=record.id,
            )
        )
    session.flush()
    audit(
        session,
        "passport",
        "imported",
        import_id=record.id,
        person_id=owner.id,
        facts=record.fact_count,
        sources=record.source_count,
        people=record.people_count,
    )
    return record


def erase(session: Session, owner: Person, record: MemoryImport) -> dict[str, int]:
    """Delete everything this import brought. Facts, files and people the owner added since are untouched."""
    facts = rows_affected(session.execute(delete(MemoryItem).where(MemoryItem.import_id == record.id)))
    # A fact that came from a later import or from chat can point at an imported file: let go of it, don't lose it.
    doc_ids = list(session.scalars(select(Document.id).where(Document.import_id == record.id)))
    if doc_ids:
        session.execute(update(MemoryItem).where(MemoryItem.document_id.in_(doc_ids)).values(document_id=None))
    docs = rows_affected(session.execute(delete(Document).where(Document.import_id == record.id)))
    contact_rows = session.execute(select(Contact.id, Contact.person_id).where(Contact.import_id == record.id)).all()
    for contact_id, person_id in contact_rows:
        session.execute(delete(Contact).where(Contact.id == contact_id))
        # The person row goes too unless something else (a linked channel, an event) already refers to them.
        try:
            with session.begin_nested():
                session.execute(delete(Person).where(Person.id == person_id))
        except IntegrityError:  # still referenced: keep the row, it is no longer the owner's contact
            pass
    record.erased_at = datetime.now(UTC)
    audit(session, "passport", "import_erased", import_id=record.id, person_id=owner.id, facts=facts, sources=docs)
    return {"facts": facts, "sources": docs, "people": len(contact_rows)}


def history(session: Session, owner_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(MemoryImport).where(MemoryImport.owner_person_id == owner_id).order_by(MemoryImport.created_at.desc())
    ).all()
    live = dict(
        session.execute(
            select(MemoryItem.import_id, func.count())
            .where(MemoryItem.import_id.is_not(None))
            .group_by(MemoryItem.import_id)
        ).all()
    )
    return [
        {
            "id": str(r.id),
            "label": r.label,
            "assistant": r.assistant,
            "at": r.created_at.isoformat(),
            "facts": r.fact_count,
            "sources": r.source_count,
            "people": r.people_count,
            "facts_left": int(live.get(r.id, 0)),
            "erased": r.erased_at is not None,
        }
        for r in rows
    ]
