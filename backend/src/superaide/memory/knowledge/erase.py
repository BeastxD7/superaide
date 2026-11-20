"""Erase is a soft delete (issue #645, owner decision 2026-11-19: keep memory, never hard-delete by default).

`erase_document` / `erase_person` hide: they set `erased_at` (and `erased_by`), and for notes also `valid_to`, so
every read path (recall, pinned facts, the notebook, vector search, the transcript) skips the rows. Nothing is
deleted; `restore_document` / `restore_person` undo it. Provenance is on the rows themselves (`document_id`,
`source_document_ids`, `source_event_ids`), so the cascade is mechanical: a note is hidden when this was its last
source, and kept (still supported by another source) otherwise.

`purge_document` / `purge_person` are the separate, explicit hard delete (the old behaviour) for the cases where
data must be removed. The routes ask for a second confirmation before calling them.

The audit log gets a line with counts and ids, never the text or the title.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, or_, select, text, update

from superaide.identity.models import Person
from superaide.memory.knowledge.store import lock_document
from superaide.memory.models import Contact, Document, DocumentChunk, Event, MemoryItem
from superaide.platform.audit import audit
from superaide.platform.db.session import get_engine, rows_affected

log = logging.getLogger(__name__)

TABLES = ("document", "document_chunk", "memory_item", "memory_embedding", "chunk_embedding", "event")


def _sole_source(m: MemoryItem, doc_id: Any, live: set[Any]) -> bool:
    """True when `doc_id` is the last live source of the note: no other file that is still kept and no chat message
    supports it. (A file that was itself erased no longer supports anything.)"""
    sources = {d for d in [m.document_id, *(m.source_document_ids or [])] if d is not None and d != doc_id}
    return not (sources & live) and not m.source_event_ids


def _live_documents(session: Any, ids: set[Any]) -> set[Any]:
    if not ids:
        return set()
    return set(session.scalars(select(Document.id).where(Document.id.in_(ids), Document.erased_at.is_(None))))


def erase_document(session: Any, owner: Person, document: Document) -> dict[str, int]:
    """Hide the document (its text and passages stay, unread by anything) and the notes learned only from it.
    A note that is also said by the owner in chat (or read from another document) stays, with that other source."""
    locked = lock_document(session, document.id)  # waits for facts being saved for it right now
    if locked is None:
        return {"facts_deleted": 0, "facts_kept": 0, "chunks": 0}
    document = locked
    doc_id = document.id
    now = datetime.now(UTC)
    facts = session.scalars(
        select(MemoryItem).where(
            or_(MemoryItem.document_id == doc_id, MemoryItem.source_document_ids.contains([doc_id]))
        )
    ).all()
    hidden = 0
    kept = 0
    live = _live_documents(
        session, {d for m in facts for d in [m.document_id, *(m.source_document_ids or [])] if d and d != doc_id}
    )
    for m in facts:
        if _sole_source(m, doc_id, live):
            if m.valid_to is None:  # one already retired (replaced, merged) stays as it was
                m.valid_to = now
                m.erased_at = now
                m.erased_by = owner.id
                hidden += 1
        else:
            kept += 1
    chunks = session.scalar(text("SELECT count(*) FROM document_chunk WHERE document_id = :d"), {"d": doc_id}) or 0
    if document.erased_at is None:
        document.erased_at = now
        document.erased_by = owner.id
        if document.removed_at is None:
            document.removed_at = now
    session.flush()
    audit(
        session,
        "memory",
        "document_erased",
        document_id=doc_id,
        soft=True,
        by=owner.id,
        facts_hidden=hidden,
        facts_kept=kept,
        chunks=chunks,
    )
    return {"facts_deleted": hidden, "facts_kept": kept, "chunks": int(chunks)}


def restore_document(session: Any, owner: Person, document: Document) -> dict[str, int]:
    """Undo an erase: the document, its passages and the notes hidden with it are read again."""
    locked = lock_document(session, document.id)
    if locked is None or locked.erased_at is None:
        return {"facts_restored": 0}
    document = locked
    ids = [
        m.id
        for m in session.scalars(
            select(MemoryItem).where(
                or_(MemoryItem.document_id == document.id, MemoryItem.source_document_ids.contains([document.id])),
                MemoryItem.erased_at.is_not(None),
            )
        )
    ]
    n = 0
    if ids:
        n = rows_affected(
            session.execute(
                update(MemoryItem)
                .where(MemoryItem.id.in_(ids), MemoryItem.erased_by == owner.id)
                .values(valid_to=None, erased_at=None, erased_by=None)
            )
        )
    document.erased_at = None
    document.erased_by = None
    document.removed_at = None
    session.flush()
    audit(session, "memory", "document_restored", document_id=document.id, by=owner.id, facts_restored=int(n))
    return {"facts_restored": int(n)}


def purge_document(session: Any, owner: Person, document: Document) -> dict[str, int]:
    """The hard delete: the document, its passages, what was learned only from it and their vectors. Cannot be
    undone. Only through an explicit, confirmed purge action, never by "erase"."""
    locked = lock_document(session, document.id)  # waits for facts being saved for it right now
    if locked is None:
        return {"facts_deleted": 0, "facts_kept": 0, "chunks": 0}
    document = locked
    doc_id = document.id
    facts = session.scalars(
        select(MemoryItem).where(
            or_(MemoryItem.document_id == doc_id, MemoryItem.source_document_ids.contains([doc_id]))
        )
    ).all()
    gone: list[Any] = []
    kept = 0
    for m in facts:
        others = [d for d in (m.source_document_ids or []) if d != doc_id]
        if m.document_id == doc_id:
            m.document_id = others.pop(0) if others else None
        m.source_document_ids = others
        if m.document_id is None and not others and not m.source_event_ids:
            gone.append(m.id)
        else:
            kept += 1
    chunks = session.scalar(text("SELECT count(*) FROM document_chunk WHERE document_id = :d"), {"d": doc_id}) or 0
    if gone:
        # Rows that point at a deleted fact keep existing; only the pointer goes. Vectors cascade.
        session.execute(update(MemoryItem).where(MemoryItem.superseded_by.in_(gone)).values(superseded_by=None))
        session.execute(update(MemoryItem).where(MemoryItem.conflicts_with_id.in_(gone)).values(conflicts_with_id=None))
        session.execute(delete(MemoryItem).where(MemoryItem.id.in_(gone)))
    session.flush()
    session.execute(
        update(MemoryItem)
        .where(MemoryItem.chunk_id.in_(select(DocumentChunk.id).where(DocumentChunk.document_id == doc_id)))
        .values(chunk_id=None)
    )
    session.delete(document)  # its passages and their vectors cascade
    session.flush()
    audit(
        session,
        "memory",
        "document_purged",
        document_id=doc_id,
        facts_deleted=len(gone),
        facts_kept=kept,
        chunks=chunks,
    )
    return {"facts_deleted": len(gone), "facts_kept": kept, "chunks": int(chunks)}


def _person_notes(person_id: Any) -> Any:
    return or_(MemoryItem.author_person_id == person_id, MemoryItem.subject_person_ids == [person_id])


def erase_person(session: Any, owner: Person, person_id: Any) -> dict[str, int]:
    """Hide what Ava holds about a third party (a contact or someone who wrote to the owner's Ava): the notes they
    asked her to keep, the notes about them, the text of their messages and the contact's notes. All of it stays
    in the database, unread, and `restore_person` brings it back."""
    if person_id == owner.id:
        raise ValueError("the owner is erased by deleting the account")
    now = datetime.now(UTC)
    n_notes = rows_affected(
        session.execute(
            update(MemoryItem)
            .where(_person_notes(person_id), MemoryItem.valid_to.is_(None))
            .values(valid_to=now, erased_at=now, erased_by=owner.id)
        )
    )
    n_msgs = rows_affected(
        session.execute(
            update(Event)
            .where(Event.resolved_person_id == person_id, Event.content != "", Event.erased_at.is_(None))
            .values(erased_at=now, erased_by=owner.id)
        )
    )
    session.execute(
        update(Contact)
        .where(Contact.person_id == person_id, Contact.owner_person_id == owner.id, Contact.notes != "")
        .values(notes_hidden=Contact.notes, notes="")
    )
    audit(session, "memory", "person_erased", person_id=person_id, soft=True, by=owner.id,
          notes_hidden=n_notes, messages_hidden=n_msgs)  # fmt: skip
    return {"notes_deleted": int(n_notes), "messages_emptied": int(n_msgs)}


def restore_person(session: Any, owner: Person, person_id: Any) -> dict[str, int]:
    """Undo `erase_person`: their notes, messages and the contact's notes are read again."""
    n_notes = rows_affected(
        session.execute(
            update(MemoryItem)
            .where(_person_notes(person_id), MemoryItem.erased_at.is_not(None), MemoryItem.erased_by == owner.id)
            .values(valid_to=None, erased_at=None, erased_by=None)
        )
    )
    n_msgs = rows_affected(
        session.execute(
            update(Event)
            .where(Event.resolved_person_id == person_id, Event.erased_at.is_not(None), Event.erased_by == owner.id)
            .values(erased_at=None, erased_by=None)
        )
    )
    session.execute(
        update(Contact)
        .where(Contact.person_id == person_id, Contact.owner_person_id == owner.id, Contact.notes_hidden != "")
        .values(notes=Contact.notes_hidden, notes_hidden="")
    )
    audit(session, "memory", "person_restored", person_id=person_id, by=owner.id,
          notes_restored=n_notes, messages_restored=n_msgs)  # fmt: skip
    return {"notes_restored": int(n_notes), "messages_restored": int(n_msgs)}


def purge_person(session: Any, owner: Person, person_id: Any) -> dict[str, int]:
    """The hard delete (what `erase_person` used to do): notes deleted, message text emptied, contact notes
    cleared. Cannot be undone. Only through an explicit, confirmed purge action."""
    if person_id == owner.id:
        raise ValueError("the owner is erased by deleting the account")
    notes = session.scalars(select(MemoryItem.id).where(_person_notes(person_id))).all()
    gone = list(notes)
    if gone:
        session.execute(update(MemoryItem).where(MemoryItem.superseded_by.in_(gone)).values(superseded_by=None))
        session.execute(update(MemoryItem).where(MemoryItem.conflicts_with_id.in_(gone)).values(conflicts_with_id=None))
        session.execute(delete(MemoryItem).where(MemoryItem.id.in_(gone)))
    n_msgs = rows_affected(
        session.execute(
            update(Event).where(Event.resolved_person_id == person_id, Event.content != "").values(content="", meta={})
        )
    )
    session.execute(
        update(Contact)
        .where(Contact.person_id == person_id, Contact.owner_person_id == owner.id)
        .values(notes="", notes_hidden="")
    )
    audit(session, "memory", "person_purged", person_id=person_id, notes_deleted=len(gone), messages_emptied=n_msgs)
    return {"notes_deleted": len(gone), "messages_emptied": int(n_msgs)}


def roll_off_third_party_messages(session: Any, owner: Person, days: int) -> int:
    """Hide the text of messages from people other than the owner that are older than `days` (soft: the rows stay).
    Off unless THIRD_PARTY_MESSAGE_RETENTION_DAYS is set: hiding by age is the owner's decision."""
    from datetime import timedelta

    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    n = rows_affected(
        session.execute(
            update(Event)
            .where(
                Event.direction == "in",
                Event.resolved_person_id.is_not(None),
                Event.resolved_person_id != owner.id,
                Event.created_at < cutoff,
                Event.content != "",
                Event.erased_at.is_(None),
            )
            .values(erased_at=datetime.now(UTC), erased_by=owner.id)
        )
    )
    if n:
        audit(session, "memory", "messages_rolled_off", messages_hidden=n, older_than_days=days, soft=True)
    return int(n)


def vacuum() -> None:
    """Reclaim the space the deleted rows held, so their bytes don't linger in the table files. Best effort:
    VACUUM can't run inside a transaction and needs table ownership, so where the app's role lacks it the
    database's own autovacuum does this instead."""
    try:
        engine = get_engine()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            existing = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
            for table in TABLES:
                if table in existing:
                    conn.execute(text(f'VACUUM "{table}"'))
    except Exception:
        log.warning("couldn't vacuum after an erase; autovacuum will", exc_info=True)
