"""/app/brain: the "Glass brain", a map of what Ava knows about the owner and why (Notebook > Glass brain).

Read-only except two owner actions that reuse the existing rules: forgetting a fact (the notebook's own
endpoint) and correcting one, which retires the old fact and writes the owner's wording as a new fact that
the owner told Ava directly. Facts reach the map through the same audience gate every other reader uses,
asked as the owner, so a fact the owner may not see is never in the response. Nothing here calls a model.
"""

from __future__ import annotations

import html
import re
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app.routes import owner_person_id
from superaide.audit import audit
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.context.audience import Audience, memory_filter
from superaide.db.models import Contact, Document, MemoryItem, Person
from superaide.db.session import require, session_scope
from superaide.identity.trust import Trust
from superaide.knowledge.store import CATEGORIES, remember_fact

MAX_FACTS = 200
MAX_FORGOTTEN = 50
MAX_SUPERSEDED = 50
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)  # asks the audience gate about a note whatever its validity dates
ORIGIN = {
    "chat": "you",
    "intro_chat": "you",
    "intro_call": "you",
    "owner_edit": "you",
    "document": "file",
    "link": "file",
    "telegram": "channel",
    "slack": "channel",
    "imported": "imported",
}
ORIGIN_LABEL = {
    "you": "You told Ava",
    "file": "From a file or link",
    "channel": "From a channel",
    "imported": "Imported",
}


class CorrectBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def owner_audience(owner_id: uuid.UUID) -> Audience:
    """Who is asking: the owner, in the app, as a verified person."""
    return Audience(person_ids={owner_id}, has_unknown=False, min_trust=int(Trust.LINKED), all_internal=True)


def _freshness(created: datetime, now: datetime) -> float:
    """1 for something just learned, fading towards 0.35 over a year, so old notes look older."""
    days = max(0.0, (now - created).total_seconds() / 86400)
    return round(max(0.35, 1 - days / 365 * 0.65), 2)


def _mentions(text: str, name: str) -> bool:
    first = name.split()[0] if name.split() else ""
    return bool(first) and len(first) > 2 and re.search(rf"\b{re.escape(first)}\b", text, re.IGNORECASE) is not None


def build_map(session: Session, owner_id: uuid.UUID) -> dict[str, Any]:
    now = datetime.now(UTC)
    owner = require(session, Person, owner_id)
    live = list(
        session.execute(
            select(MemoryItem, Document)
            .outerjoin(Document, Document.id == MemoryItem.document_id)
            .where(
                MemoryItem.subject_person_ids.contains([owner_id]),
                memory_filter(owner_audience(owner_id), now),
            )
            .order_by(MemoryItem.created_at.desc())
            .limit(MAX_FACTS)
        ).all()
    )
    people = session.execute(
        select(Contact, Person)
        .join(Person, Person.id == Contact.person_id)
        .where(Contact.owner_person_id == owner_id, Contact.removed_at.is_(None))
        .order_by(Person.display_name)
    ).all()
    nodes: list[dict[str, Any]] = [{"id": "owner", "type": "owner", "label": owner.display_name}]
    edges: list[dict[str, str]] = []
    for cat in sorted({m.category for m, _ in live}):
        nodes.append({"id": f"topic:{cat}", "type": "topic", "label": cat})
    for contact, person in people:
        nodes.append(
            {
                "id": f"person:{contact.id}",
                "type": "person",
                "label": person.display_name,
                "relationship": contact.relationship,
                "vip": contact.vip,
            }
        )
        edges.append({"from": "owner", "to": f"person:{contact.id}", "kind": "knows"})
    seen_docs: set[uuid.UUID] = set()
    counts: Counter[str] = Counter()
    for m, doc in live:
        origin = ORIGIN.get(m.source, "you")
        counts[origin] += 1
        nodes.append(
            {
                "id": f"fact:{m.id}",
                "type": "fact",
                "label": m.content,
                "category": m.category,
                "origin": origin,
                "source": m.source,
                "document": doc.title if doc else None,
                "confidence": m.confidence,
                "freshness": _freshness(m.created_at, now),
                "learned": m.created_at.isoformat(),
                "shareable": m.allowed_kind == "public",
            }
        )
        edges.append({"from": f"topic:{m.category}", "to": f"fact:{m.id}", "kind": "about"})
        if doc is not None:
            if doc.id not in seen_docs:
                seen_docs.add(doc.id)
                nodes.append(
                    {
                        "id": f"source:{doc.id}",
                        "type": "source",
                        "label": doc.title or doc.source or "A shared file",
                        "kind": doc.kind,
                        "origin": "file",
                    }
                )
            edges.append({"from": f"source:{doc.id}", "to": f"fact:{m.id}", "kind": "learned_from"})
        for contact, person in people:
            if _mentions(m.content, person.display_name):
                edges.append({"from": f"fact:{m.id}", "to": f"person:{contact.id}", "kind": "mentions"})
    for cat in {m.category for m, _ in live}:
        edges.append({"from": "owner", "to": f"topic:{cat}", "kind": "has"})
    # A retired note is still gated by who may see it (validity aside), so the shelf and the strike-throughs can
    # never show something the owner may not see.
    gate = memory_filter(owner_audience(owner_id), EPOCH)
    live_ids = {m.id for m, _ in live}
    replaced = session.scalars(
        select(MemoryItem)
        .where(
            MemoryItem.subject_person_ids.contains([owner_id]),
            MemoryItem.valid_to.is_not(None),
            MemoryItem.valid_to <= now,
            MemoryItem.superseded_by.in_(live_ids) if live_ids else MemoryItem.id.is_(None),
            gate,
        )
        .order_by(MemoryItem.valid_to.desc())
        .limit(MAX_SUPERSEDED)
    ).all()
    for old in replaced:
        nodes.append(
            {
                "id": f"fact:{old.id}",
                "type": "fact",
                "label": old.content,
                "category": old.category,
                "origin": ORIGIN.get(old.source, "you"),
                "source": old.source,
                "document": None,
                "confidence": old.confidence,
                "freshness": 0.5,
                "learned": old.created_at.isoformat(),
                "shareable": False,
                "superseded": True,
                "replaced_by": f"fact:{old.superseded_by}",
                "replaced_at": old.valid_to.isoformat() if old.valid_to else None,
            }
        )
        edges.append({"from": f"fact:{old.id}", "to": f"fact:{old.superseded_by}", "kind": "replaced_by"})
    forgotten = session.scalars(
        select(MemoryItem)
        .where(MemoryItem.subject_person_ids.contains([owner_id]), MemoryItem.valid_to.is_not(None), gate)
        .where(MemoryItem.valid_to <= now)
        .order_by(MemoryItem.valid_to.desc())
        .limit(MAX_FORGOTTEN)
    ).all()
    return {
        "nodes": nodes,
        "edges": edges,
        "forgotten": [
            {"id": str(m.id), "text": m.content, "category": m.category, "forgotten_at": m.valid_to.isoformat()}
            for m in forgotten
            if m.valid_to
        ],
        "origins": {k: {"label": ORIGIN_LABEL[k], "count": counts.get(k, 0)} for k in ORIGIN_LABEL},
        "truncated": len(live) >= MAX_FACTS,
    }


def share_svg(session: Session, owner_id: uuid.UUID) -> str:
    """A shareable picture of the shape of the memory: a ring of topics sized by how much is in each, coloured
    by where it came from. Only fixed words and numbers; no names, no note text, no file names."""
    live = session.scalars(
        select(MemoryItem).where(
            MemoryItem.subject_person_ids.contains([owner_id]), memory_filter(owner_audience(owner_id))
        )
    ).all()
    by_topic: Counter[str] = Counter(m.category for m in live)
    colours = {"you": "#7c5cff", "file": "#0ea5e9", "channel": "#10b981", "imported": "#f59e0b"}
    import math

    topics = [c for c in CATEGORIES if by_topic.get(c)]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" role="img" aria-label="Shape of memory">']
    parts.append('<rect width="400" height="400" fill="#faf9ff"/><circle cx="200" cy="200" r="22" fill="#2b2250"/>')
    for i, cat in enumerate(topics):
        ang = 2 * math.pi * i / max(1, len(topics))
        cx, cy = 200 + 120 * math.cos(ang), 200 + 120 * math.sin(ang)
        parts.append(f'<line x1="200" y1="200" x2="{cx:.1f}" y2="{cy:.1f}" stroke="#d9d3f5"/>')
        members = [m for m in live if m.category == cat]
        for j, m in enumerate(members[:24]):
            a2 = 2 * math.pi * j / min(24, len(members))
            r = 14 + 4 * math.sqrt(len(members))
            fx, fy = cx + r * math.cos(a2), cy + r * math.sin(a2)
            colour = colours[ORIGIN.get(m.source, "you")]
            parts.append(f'<circle cx="{fx:.1f}" cy="{fy:.1f}" r="4" fill="{colour}"/>')
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="9" fill="#2b2250"/>')
        label = html.escape(f"{cat} {by_topic[cat]}")
        parts.append(
            f'<text x="{cx:.1f}" y="{cy + 5:.1f}" font-size="10" text-anchor="middle" fill="#2b2250">{label}</text>'
        )
    parts.append(f'<text x="200" y="388" font-size="11" text-anchor="middle">{len(live)} things Ava knows</text></svg>')
    return "".join(parts)


def make_brain_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/brain",
        tags=["Owner: glass brain"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "",
        summary="Map of what Ava knows",
        description=(
            "Nodes and edges for people, topics, facts and the files they came from, plus what the owner made Ava "
            "forget. Facts pass the audience gate as the owner. Read-only."
        ),
    )
    def brain(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return build_map(s, person_id)

    @router.post(
        "/facts/{memory_id}/correct",
        summary="Correct a fact",
        description="Retires the fact and saves the owner's wording as a new one the owner told Ava directly.",
    )
    def correct(
        memory_id: uuid.UUID, body: CorrectBody, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, str]:
        text = " ".join(body.text.split())
        if not text:
            raise HTTPException(422, "Write what is true.")
        with session_scope() as s:
            old = s.get(MemoryItem, memory_id)
            if old is None or person_id not in (old.subject_person_ids or []) or old.valid_to is not None:
                raise HTTPException(404, "Not found in your notebook")
            owner = require(s, Person, person_id)
            old.valid_to = datetime.now(UTC)
            s.flush()
            new = remember_fact(s, owner=owner, content=text, category=old.category, source="owner_edit")
            if new is None:
                raise HTTPException(409, "Ava already knows that.")
            old.superseded_by = new.id  # the map shows the old wording struck through, pointing at the new one
            audit(s, "app", "memory_corrected", memory_id=old.id, new_memory_id=new.id, person_id=person_id)
            return {"id": str(new.id)}

    @router.get(
        "/share.svg",
        summary="Shareable shape of the memory",
        description="A picture of the shape of the memory: counts and colours only, no names, notes or file names.",
        response_class=Response,
    )
    def share(person_id: uuid.UUID = Depends(owner_person_id)) -> Response:
        with session_scope() as s:
            svg = share_svg(s, person_id)
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    return router
