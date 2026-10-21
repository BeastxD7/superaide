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

from sqlalchemy import select

from superaide.audit import audit
from superaide.db.models import Document, Person
from superaide.db.session import session_scope
from superaide.knowledge.extract import Extracted
from superaide.knowledge.facts import extract_facts
from superaide.knowledge.store import remember_fact


@dataclass
class IngestResult:
    document_id: uuid.UUID
    title: str
    facts: int
    duplicate: bool
    status: str
    message: str


def ingest(agent: Any, owner_id: uuid.UUID, extracted: Extracted, *, size_bytes: int = 0) -> IngestResult:
    from superaide.factory import uses_real_model

    source_kind = "link" if extracted.kind in {"web", "github"} else "document"
    digest = hashlib.sha256((extracted.source + "\n" + extracted.text).encode()).hexdigest()

    # 1. Keep the text (or find that it was already shared).
    with session_scope() as s:
        owner = s.get(Person, owner_id)
        existing = s.scalar(
            select(Document).where(
                Document.owner_person_id == owner_id, Document.sha256 == digest, Document.removed_at.is_(None)
            )
        )
        if existing is not None:
            return IngestResult(existing.id, existing.title, 0, True, existing.status, "You've already shared this.")
        old = s.scalar(select(Document).where(Document.owner_person_id == owner_id, Document.sha256 == digest))
        if old is not None:  # shared before, then removed: bring it back fresh
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
        audit(s, "knowledge", "document_added", document_id=doc.id, kind=doc.kind, chars=len(doc.text))

    # 2. Read it (no transaction open while the model works).
    facts = extract_facts(agent, owner_name=owner_name, text=extracted.text, source=title)

    # 3. Keep the facts, linked to the document.
    with session_scope() as s:
        owner = s.get(Person, owner_id)
        doc = s.get(Document, doc_id)
        n = 0
        for f in facts:
            if remember_fact(
                s,
                owner=owner,
                content=f.content,
                category=f.category,
                source=source_kind,
                document=doc,
                confidence=f.confidence,
            ):
                n += 1
        if uses_real_model(agent.settings):
            doc.status = "read"
            message = (
                f"I've read {title}. I picked up {n} {'thing' if n == 1 else 'things'}; they're in my notebook."
                if n
                else f"I've read {title}, but didn't find anything new about you in it."
            )
        else:
            message = f"I've saved {title}. I'll read it properly once my Claude brain is connected."
        return IngestResult(doc_id, title, n, False, doc.status, message)


def read_pending(agent: Any, owner_id: uuid.UUID) -> int:
    """Read facts out of documents saved while offline. Returns how many documents were read."""
    from superaide.factory import uses_real_model

    if not uses_real_model(agent.settings):
        return 0
    with session_scope() as s:
        pending = [
            (d.id, d.title or d.source, d.text, d.kind)
            for d in s.scalars(
                select(Document).where(
                    Document.owner_person_id == owner_id, Document.status == "stored", Document.removed_at.is_(None)
                )
            )
        ]
        owner_name = s.get(Person, owner_id).display_name
    for doc_id, title, text, kind in pending:
        facts = extract_facts(agent, owner_name=owner_name, text=text, source=title)
        with session_scope() as s:
            owner, doc = s.get(Person, owner_id), s.get(Document, doc_id)
            for f in facts:
                remember_fact(
                    s,
                    owner=owner,
                    content=f.content,
                    category=f.category,
                    source="link" if kind in {"web", "github"} else "document",
                    document=doc,
                    confidence=f.confidence,
                )
            doc.status = "read"
    return len(pending)
