"""Files and links the owner shares: store the text, then read facts out of it.

Three short steps so no database transaction is held open while the model
reads: save the Document, extract facts (model call, outside any transaction),
then save the facts and tell the owner what was learned.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from superaide.audit import audit
from superaide.brain import keyhealth
from superaide.db.models import Document, MemoryItem, Person
from superaide.db.session import require, session_scope
from superaide.knowledge.extract import Extracted
from superaide.knowledge.facts import extract_facts
from superaide.knowledge.learn import neighbours
from superaide.knowledge.store import lock_document, remember_fact, supersede
from superaide.proactive.notify import notify_owner


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
        doc_id, title, owner_name = doc.id, doc.title or doc.source, owner.display_name
        known = [(m.id, m.content) for m in neighbours(s, owner, f"{title} {extracted.text[:300]}")]
        audit(s, "knowledge", "document_added", document_id=doc.id, kind=doc.kind, chars=len(doc.text))

    # 2. Read it (no transaction open while the model works).
    facts = extract_facts(agent, owner_name=owner_name, text=extracted.text, source=title, known=[k for _, k in known])

    # 3. Keep the facts, linked to the document.
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
        n = save_facts(s, owner, doc, facts or [], source_kind, dict(enumerate((i for i, _ in known), 1)))
        if facts is None:  # reading failed: keep it as saved so it is tried again
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


def save_facts(
    session: Any, owner: Person, doc: Document, facts: list[Any], source: str, known_ids: dict[int, uuid.UUID]
) -> int:
    """Keep a document's facts. A fact that says a known one is untrue retires it only when the document is
    at least as trusted (knowledge/trust.py); otherwise it waits for the owner. A page's urgency, sharing
    or availability "rules" are never applied: they wait in the review queue (a page anyone can edit must
    not tell Ava what is urgent or shareable)."""
    n = 0
    for f in facts:
        if f.same_as:
            continue  # already known: saying it again adds nothing
        item = remember_fact(
            session, owner=owner, content=f.content, category=f.category, source=source, document=doc,
            confidence=f.confidence,
        )  # fmt: skip
        if item is None:
            continue
        n += 1
        old_id = known_ids.get(f.replaces or 0)
        old = session.get(MemoryItem, old_id) if old_id else None
        if old is not None and item.conflict_status == "none":
            supersede(session, old, item)
    return n


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
            (d.id, d.title or d.source, d.text, d.kind)
            for d in s.scalars(
                select(Document).where(
                    Document.owner_person_id == owner_id, Document.status == "stored", Document.removed_at.is_(None)
                )
            )
        ]
        owner_name = require(s, Person, owner_id).display_name
    results: list[ReadResult] = []
    for doc_id, title, text, kind in pending:
        facts = extract_facts(agent, owner_name=owner_name, text=text, source=title)
        if facts is None:
            results.append(ReadResult(doc_id, title, 0, False))  # still saved, tried again later
            if keyhealth.any_rejected():
                break  # the key was refused: the next document would be refused too
            continue
        source = "link" if kind in {"web", "github"} else "document"
        with session_scope() as s:
            owner, doc = require(s, Person, owner_id), lock_document(s, doc_id)
            if doc is None or doc.status != "stored" or doc.removed_at is not None:
                continue  # read or removed while the model worked
            n = save_facts(s, owner, doc, facts, source, {})
            doc.status = "read"
            note = (
                f"I've read {title}: {n} {'fact' if n == 1 else 'facts'} saved."
                if n
                else f"I've read {title}, but didn't find anything new about you in it."
            )
            meta = {"kind": "document_read", "document_id": str(doc_id), "facts": n}
            notify_owner(s, owner, note, meta, channel="app")  # in the app chat, for the owner only
            results.append(ReadResult(doc_id, title, n, True))
    return results
