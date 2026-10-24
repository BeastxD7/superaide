"""Export: everything Ava knows about the owner, as a passport zip.

Only the owner's own data leaves: facts about the owner alone, shared by no one else; the files
and links the owner shared; the people the owner listed (a name, how they're related, the
owner's own notes, never a contact detail or a channel ID); and the owner's preferences for
reading. Facts the owner told Ava to forget are gone from the database, so they are not here.
Nothing in the export is another person's private data, and nothing in the export is a secret
(no credentials, tokens, session data, or other owners' rows).
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Contact, Document, MemoryItem, Person
from superaide.features.passport.schema import FORMAT, SCHEMA_VERSION
from superaide.knowledge.store import preferences_for


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def owner_facts(session: Session, owner_id: uuid.UUID) -> list[MemoryItem]:
    """Live facts that are about the owner and no one else, and that no other person may see."""
    rows = session.scalars(
        select(MemoryItem)
        .where(MemoryItem.subject_person_ids == [owner_id], MemoryItem.valid_to.is_(None))
        .order_by(MemoryItem.created_at, MemoryItem.id)
    ).all()
    # Facts shared with a named other person were told to Ava in someone else's conversation.
    return [m for m in rows if not (set(m.allowed_person_ids or []) - {owner_id})]


def build(session: Session, owner_id: uuid.UUID, *, include_sources: bool = True) -> tuple[bytes, dict[str, Any]]:
    owner = session.get(Person, owner_id)
    assert owner is not None
    facts = owner_facts(session, owner_id)
    docs = (
        list(
            session.scalars(
                select(Document)
                .where(Document.owner_person_id == owner_id, Document.removed_at.is_(None))
                .order_by(Document.created_at, Document.id)
            )
        )
        if include_sources
        else []
    )
    doc_keys = {d.id: f"src_{i}" for i, d in enumerate(docs, 1)}
    contacts = session.execute(
        select(Contact, Person)
        .join(Person, Person.id == Contact.person_id)
        .where(Contact.owner_person_id == owner_id, Contact.removed_at.is_(None))
        .order_by(Person.display_name)
    ).all()

    fact_lines = [
        {
            "id": f"fact_{i}",
            "content": m.content,
            "category": m.category,
            "confidence": m.confidence,
            "origin": "imported" if m.source == "imported" else m.source,
            "valid_from": _iso(m.valid_from),
            "valid_to": None,
            "source_id": doc_keys.get(m.document_id) if m.document_id else None,
            # Informational: an import never reads this, the importing Ava decides who may see a fact.
            "shareable": m.allowed_kind == "public",
        }
        for i, m in enumerate(facts, 1)
    ]
    source_lines = [
        {
            "id": doc_keys[d.id],
            "kind": d.kind,
            "title": d.title,
            "source": d.source,
            "text": d.text,
            "warnings": list(d.warnings or []),
            "shared_at": _iso(d.created_at),
        }
        for d in docs
    ]
    people_lines = [{"name": p.display_name, "relationship": c.relationship, "notes": c.notes} for c, p in contacts]
    pref = preferences_for(session, owner)
    prefs = {
        "urgent_topics": list(pref.urgent_topics),
        "digest": pref.digest,
        "quiet_start": pref.quiet_start,
        "quiet_end": pref.quiet_end,
        "timezone": pref.timezone,
        "shareable": list(pref.shareable),
        "never_share": pref.never_share,
        "note": "For reading and for moving by hand. An import never changes the preferences you already set.",
    }
    counts = {"facts": len(fact_lines), "sources": len(source_lines), "people": len(people_lines)}
    manifest = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "exported_at": _iso(datetime.now(UTC)),
        "assistant": "Superaide Ava",
        "owner": owner.display_name,
        "counts": counts,
        "files": ["facts.jsonl", "sources.jsonl", "people.jsonl", "preferences.json"],
    }

    def jsonl(rows: list[dict[str, Any]]) -> str:
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("facts.jsonl", jsonl(fact_lines))
        zf.writestr("sources.jsonl", jsonl(source_lines))
        zf.writestr("people.jsonl", jsonl(people_lines))
        zf.writestr("preferences.json", json.dumps(prefs, indent=2, ensure_ascii=False))
    audit(session, "passport", "exported", person_id=owner_id, **counts)
    return buf.getvalue(), counts
