"""Find what Ava knows about a question: notes and past messages, behind the audience gate.

Keyword search (Postgres full-text, English stemming) ranked by relevance and then
recency. Every result carries an `ava://` id so a reply can cite it and the owner can
click through. The gate lives in the SQL: a note is visible under `memory_filter`, and a
message only if everyone in the current audience was in that message's audience.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from superaide.context.audience import Audience, memory_filter
from superaide.db.models import Context, Event, MemoryItem

LANG = "english"


@dataclass
class Source:
    uri: str  # ava://memory/<id> | ava://msg/<provider>/<event id>
    kind: str  # memory | message
    title: str
    text: str
    at: datetime


def _tsquery(query: str) -> Any:
    return func.websearch_to_tsquery(LANG, query)


def recall_memories(session: Session, aud: Audience, query: str, limit: int = 6) -> list[Source]:
    doc = func.to_tsvector(LANG, MemoryItem.content)
    q = _tsquery(query)
    rows = session.execute(
        select(MemoryItem, func.ts_rank(doc, q).label("rank"))
        .where(memory_filter(aud), doc.op("@@")(q))
        .order_by(desc("rank"), MemoryItem.created_at.desc())
        .limit(limit)
    ).all()
    return [
        Source(
            uri=f"ava://memory/{m.id}",
            kind="memory",
            title=f"Note · {m.category}" + (f" · from {m.source}" if m.source else ""),
            text=m.content,
            at=m.created_at,
        )
        for m, _ in rows
    ]


def recall_messages(session: Session, aud: Audience, query: str, limit: int = 6) -> list[Source]:
    if aud.has_unknown or not aud.person_ids:
        return []  # only for a known audience, and only what they all saw
    doc = func.to_tsvector(LANG, Event.content)
    q = _tsquery(query)
    rows = session.execute(
        select(Event, Context.provider, func.ts_rank(doc, q).label("rank"))
        .join(Context, Context.id == Event.context_id)
        .where(
            and_(Event.audience_person_ids.contains(sorted(aud.person_ids)), doc.op("@@")(q)),
            Event.content != "",
        )
        .order_by(desc("rank"), Event.created_at.desc())
        .limit(limit)
    ).all()
    out = []
    for e, provider, _ in rows:
        who = "You" if e.direction == "in" else "Ava"
        out.append(
            Source(
                uri=f"ava://msg/{provider}/{e.id}",
                kind="message",
                title=f"{who} · {provider} · {e.created_at:%d %b %Y}",
                text=e.content[:1000],
                at=e.created_at,
            )
        )
    return out


def recall(
    session: Session, aud: Audience, query: str, limit: int = 8, exclude_event: uuid.UUID | None = None
) -> list[Source]:
    """Notes first (they are distilled), then messages; never more than `limit`."""
    query = " ".join(query.split())[:300]
    if not query:
        return []
    notes = recall_memories(session, aud, query, limit=limit)
    msgs = [m for m in recall_messages(session, aud, query, limit=limit) if not m.uri.endswith(str(exclude_event))]
    return (notes + msgs)[:limit]
