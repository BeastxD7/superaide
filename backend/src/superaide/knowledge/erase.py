"""Real erasure (issue #48): taking a source away deletes its text, passages, facts and vectors.

`retire_document` only hides (it sets `valid_to` and `removed_at`; the text stays in the database). Erase
is the other action: nothing derived from the source is left, except a fact that something else also
supports. Provenance is on the rows themselves (`document_id`, `source_document_ids`, `source_event_ids`),
so the cascade is mechanical: a fact is deleted when this was its last source.

The audit log gets a line with counts and ids, never the text or the title.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, or_, select, text, update

from superaide.knowledge.store import lock_document
from superaide.platform.audit import audit
from superaide.platform.db.models import Contact, Document, DocumentChunk, Event, MemoryItem, Person
from superaide.platform.db.session import get_engine, rows_affected

log = logging.getLogger(__name__)

TABLES = ("document", "document_chunk", "memory_item", "memory_embedding", "chunk_embedding", "event")


def erase_document(session: Any, owner: Person, document: Document) -> dict[str, int]:
    """Delete the document, its passages and what was learned only from it. A fact that is also said by
    the owner in chat (or read from another document) stays, with that other source."""
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
        "document_erased",
        document_id=doc_id,
        facts_deleted=len(gone),
        facts_kept=kept,
        chunks=chunks,
    )
    return {"facts_deleted": len(gone), "facts_kept": kept, "chunks": int(chunks)}


def erase_person(session: Any, owner: Person, person_id: Any) -> dict[str, int]:
    """Erase what Ava holds about a third party (a contact or someone who wrote to the owner's Ava): the
    notes they asked her to keep, the notes about them, and the text of their messages. The conversation
    rows stay, emptied, so counts and the audit trail still add up."""
    if person_id == owner.id:
        raise ValueError("the owner is erased by deleting the account")
    notes = session.scalars(
        select(MemoryItem.id).where(
            or_(MemoryItem.author_person_id == person_id, MemoryItem.subject_person_ids == [person_id])
        )
    ).all()
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
        update(Contact).where(Contact.person_id == person_id, Contact.owner_person_id == owner.id).values(notes="")
    )
    audit(session, "memory", "person_erased", person_id=person_id, notes_deleted=len(gone), messages_emptied=n_msgs)
    return {"notes_deleted": len(gone), "messages_emptied": int(n_msgs)}


def roll_off_third_party_messages(session: Any, owner: Person, days: int) -> int:
    """Empty the text of messages from people other than the owner that are older than `days`. Off unless
    THIRD_PARTY_MESSAGE_RETENTION_DAYS is set: deleting by age is the owner's decision."""
    from datetime import UTC, datetime, timedelta

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
            )
            .values(content="", meta={})
        )
    )
    if n:
        audit(session, "memory", "messages_rolled_off", messages_emptied=n, older_than_days=days)
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
