"""Files and links the owner shares: store the text, then read facts out of it.

Three short steps so no database transaction is held open while the model
reads: save the Document, extract facts (model call, outside any transaction),
then save the facts and tell the owner what was learned.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from superaide.audit import audit
from superaide.brain import keyhealth
from superaide.db.models import Document, DocumentChunk, MemoryItem, Person
from superaide.db.session import require, session_scope
from superaide.knowledge import chunking
from superaide.knowledge.extract import Extracted
from superaide.knowledge.facts import extract_from_chunks
from superaide.knowledge.learn import neighbours
from superaide.knowledge.store import lock_document, remember_fact, supersede
from superaide.proactive.notify import notify_owner

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    document_id: uuid.UUID
    title: str
    facts: int
    duplicate: bool
    status: str
    message: str


def ingest(agent: Any, owner_id: uuid.UUID, extracted: Extracted, *, size_bytes: int = 0) -> IngestResult:
    from superaide.factory import brain_ready

    source_kind = "link" if extracted.kind in {"web", "github"} else "document"
    digest = hashlib.sha256((extracted.source + "\n" + extracted.text).encode()).hexdigest()

    # 1. Keep the text (or find that it was already shared).
    with session_scope() as s:
        owner = require(s, Person, owner_id)
        existing = s.scalar(
            select(Document).where(
                Document.owner_person_id == owner_id, Document.sha256 == digest, Document.removed_at.is_(None)
            )
        )
        if existing is not None:
            return IngestResult(existing.id, existing.title, 0, True, existing.status, "You've already shared this.")
        old = s.scalar(select(Document).where(Document.owner_person_id == owner_id, Document.sha256 == digest))
        if old is not None:  # shared before, then removed: bring it back fresh
            # Its facts were retired when it was removed; erase them with it.
            s.execute(delete(MemoryItem).where(MemoryItem.document_id == old.id))
            s.delete(old)
            s.flush()
        doc = Document(
            owner_person_id=owner_id,
            kind=extracted.kind,
            title=extracted.title[:300],
            source=extracted.source[:2000],
            sha256=digest,
            size_bytes=size_bytes,
            text=extracted.text,
            status="stored",
            warnings=list(extracted.warnings),
        )
        s.add(doc)
        s.flush()
        chunks = store_chunks(s, doc)
        doc_id, title, owner_name = doc.id, doc.title or doc.source, owner.display_name
        known = [(m.id, m.content) for m in neighbours(s, owner, f"{title} {extracted.text[:300]}")]
        audit(s, "knowledge", "document_added", document_id=doc.id, kind=doc.kind, chars=len(doc.text))

    # 2. Read it (no transaction open while the model works).
    facts: list[Any] | None
    if not extracted.text.strip() or not brain_ready(agent.settings):
        facts, complete = [], True  # nothing to read, or no model yet: saved as is
    else:
        read = extract_from_chunks(
            agent, owner_name=owner_name, chunks=chunks, source=title, known=[k for _, k in known]
        )
        facts, complete = read if read is not None else (None, False)

    # 3. Keep the facts, linked to the document.
    try:
        return _keep_facts(
            agent, owner_id, doc_id, title, source_kind, facts, dict(enumerate((i for i, _ in known), 1)), complete
        )
    except Exception as exc:  # not `read`: say it failed, keep the text, offer a retry
        mark_failed(doc_id, title, exc)
        return IngestResult(doc_id, title, 0, False, "failed", f"I saved {title} but couldn't read it: try again.")


def _keep_facts(
    agent: Any,
    owner_id: uuid.UUID,
    doc_id: uuid.UUID,
    title: str,
    source_kind: str,
    facts: list[Any] | None,
    known_ids: dict[int, uuid.UUID],
    complete: bool = True,
) -> IngestResult:
    from superaide.factory import brain_ready

    with session_scope() as s:
        owner = require(s, Person, owner_id)
        locked = lock_document(s, doc_id)
        if locked is None or locked.removed_at is not None:  # the owner removed it while the model was reading
            return IngestResult(
                doc_id,
                title,
                0,
                False,
                "removed",
                f"{title} was removed while I was reading it. I kept nothing from it.",
            )
        doc = locked
        n = save_facts(s, owner, doc, facts or [], source_kind, known_ids)
        if facts is None or not complete:  # reading failed (or part of it): keep it as saved, tried again
            message = f"I've saved {title}, but couldn't read it just now. I'll try again shortly."
        elif brain_ready(agent.settings):
            doc.status = "read"
            message = (
                f"I've read {title}. I picked up {n} {'thing' if n == 1 else 'things'}; they're in my notebook."
                if n
                else f"I've read {title}, but didn't find anything new about you in it."
            )
        else:
            message = f"I've saved {title}. I'll read it properly once an AI model is connected."
        return IngestResult(doc_id, title, n, False, doc.status, message)


def store_chunks(session: Any, doc: Document) -> list[tuple[uuid.UUID, str]]:
    """Split the document's text into passages and keep them (once). Returns (id, text) in order."""
    existing = session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id).order_by(DocumentChunk.ordinal)
    ).all()
    if not existing:
        for i, piece in enumerate(chunking.split(doc.text)):
            session.add(DocumentChunk(document_id=doc.id, ordinal=i, text=piece))
        session.flush()
        existing = session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == doc.id).order_by(DocumentChunk.ordinal)
        ).all()
        if existing:
            from superaide.context import vectors

            vectors.schedule_chunks(session, doc.id)
    return [(c.id, c.text) for c in existing]


def save_facts(
    session: Any, owner: Person, doc: Document, facts: list[Any], source: str, known_ids: dict[int, uuid.UUID]
) -> int:
    """Keep a document's facts. A fact that says a known one is untrue retires it only when the document is
    at least as trusted (knowledge/trust.py); otherwise it waits for the owner. A page's urgency, sharing
    or availability "rules" are never applied: they wait in the review queue (a page anyone can edit must
    not tell Ava what is urgent or shareable)."""
    n = 0
    for f in facts:
        f, chunk_id = f if isinstance(f, tuple) else (f, None)
        if f.same_as:
            continue  # already known: saying it again adds nothing
        item = remember_fact(
            session, owner=owner, content=f.content, category=f.category, source=source, document=doc,
            confidence=f.confidence, importance=f.importance, time_bound=f.time_bound,
        )  # fmt: skip
        if item is None:
            continue
        n += 1
        item.chunk_id = chunk_id
        old_id = known_ids.get(f.replaces or 0)
        old = session.get(MemoryItem, old_id) if old_id else None
        if old is not None and item.conflict_status == "none":
            supersede(session, old, item)
    return n


def mark_failed(doc_id: uuid.UUID, title: str, exc: BaseException) -> None:
    """Saving what was learnt from a document failed (the model's own failure stays `stored` and is retried).
    Say so, in a fresh transaction: the document becomes `failed` instead of `read` with nothing to show, the
    owner is told in the app, and Retry (POST /app/intro/documents/{id}/retry) reads it again. Only the error's
    type is kept: its text can hold SQL or values."""
    log.error("couldn't save the facts of a document: %s", type(exc).__name__, exc_info=exc)
    try:
        with session_scope() as s:
            doc = lock_document(s, doc_id)
            if doc is None or doc.removed_at is not None:
                return
            doc.status = "failed"
            doc.warnings = [*(doc.warnings or []), f"Reading failed ({type(exc).__name__})."][-5:]
            audit(s, "knowledge", "document_read_failed", document_id=doc_id, error=type(exc).__name__)
            owner = s.get(Person, doc.owner_person_id)
            if owner is not None:
                notify_owner(
                    s, owner, f"I couldn't read {title}: try again.",
                    {"kind": "document_failed", "document_id": str(doc_id)}, channel="app",
                )  # fmt: skip
    except Exception:
        log.exception("couldn't record that a document failed")


def retry_document(agent: Any, owner_id: uuid.UUID, document_id: uuid.UUID) -> ReadResult | None:
    """Put a failed document back to be read and read it now. None: no such document of this owner's."""
    with session_scope() as s:
        doc = lock_document(s, document_id)
        if doc is None or doc.owner_person_id != owner_id or doc.removed_at is not None:
            return None
        if doc.status == "failed":
            doc.status = "stored"
    results = [r for r in read_documents(agent, owner_id) if r.document_id == document_id]
    return results[0] if results else ReadResult(document_id, "", 0, False)


@dataclass
class ReadResult:
    document_id: uuid.UUID
    title: str
    facts: int
    read: bool  # False: the model couldn't read it just now; it stays saved and is tried again


def read_pending(agent: Any, owner_id: uuid.UUID) -> int:
    """Read documents saved while offline. Returns how many it tried."""
    return len(read_documents(agent, owner_id))


def read_documents(agent: Any, owner_id: uuid.UUID) -> list[ReadResult]:
    """Read facts out of documents saved while offline, and tell the owner in chat about each one read.
    Nothing is held open while the model works; a document that can't be read stays `stored`."""
    from superaide.factory import brain_ready

    if not brain_ready(agent.settings):
        return []
    with session_scope() as s:
        pending = [
            (d.id, d.title or d.source, store_chunks(s, d), d.kind)
            for d in s.scalars(
                select(Document).where(
                    Document.owner_person_id == owner_id, Document.status == "stored", Document.removed_at.is_(None)
                )
            )
        ]
        owner_name = require(s, Person, owner_id).display_name
    results: list[ReadResult] = []
    for doc_id, title, chunks, kind in pending:
        read = extract_from_chunks(agent, owner_name=owner_name, chunks=chunks, source=title) if chunks else ([], True)
        facts, complete = read if read is not None else (None, False)
        if facts is None:
            results.append(ReadResult(doc_id, title, 0, False))  # still saved, tried again later
            if keyhealth.any_rejected():
                break  # the key was refused: the next document would be refused too
            continue
        source = "link" if kind in {"web", "github"} else "document"
        try:
            saved = _save_read(owner_id, doc_id, title, source, facts, complete)
        except Exception as exc:
            mark_failed(doc_id, title, exc)
            results.append(ReadResult(doc_id, title, 0, False))
            continue
        if saved is not None:
            results.append(saved)
    return results


def _save_read(
    owner_id: uuid.UUID, doc_id: uuid.UUID, title: str, source: str, facts: list[Any], complete: bool = True
) -> ReadResult | None:
    with session_scope() as s:
        owner, doc = require(s, Person, owner_id), lock_document(s, doc_id)
        if doc is None or doc.status != "stored" or doc.removed_at is not None:
            return None  # read or removed while the model worked
        n = save_facts(s, owner, doc, facts, source, {})
        if not complete:
            return ReadResult(doc_id, title, n, False)  # part is read and kept; the rest is retried
        doc.status = "read"
        note = (
            f"I've read {title}: {n} {'fact' if n == 1 else 'facts'} saved."
            if n
            else f"I've read {title}, but didn't find anything new about you in it."
        )
        meta = {"kind": "document_read", "document_id": str(doc_id), "facts": n}
        notify_owner(s, owner, note, meta, channel="app")  # in the app chat, for the owner only
        return ReadResult(doc_id, title, n, True)
