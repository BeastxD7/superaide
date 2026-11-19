"""/app/people/{contact_id}: the owner's page for one person (issue #500).

What Ava holds about them, where each fact came from and who may be told it, and the owner's own fields
(relationship, VIP, notes) which the owner can edit. Forgetting a fact is a soft delete (`valid_to` is set, the
row stays for the audit trail and can no longer reach a prompt). Owner only; every read and write is scoped to a
contact on the signed-in owner's own list, so another owner's contact, a stranger's id or a made-up id is a 404.

Nothing here lets a model write anything, and nothing here reads message content to decide who someone is.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from superaide.app.deps import owner_person_id
from superaide.identity.auth.deps import reject_nul_text, require_csrf_header
from superaide.identity.models import ChannelIdentity, Person
from superaide.memory.models import Contact, Document, MemoryItem
from superaide.platform.audit import audit
from superaide.platform.db.session import session_scope

MAX_FACTS = 200
SOURCE_LABELS = {
    "chat": "Told to Ava in the app",
    "intro_chat": "Told to Ava in the introduction",
    "intro_call": "Said in the introduction call",
    "document": "Read from a file",
    "link": "Read from a link",
    "telegram": "Told to Ava on Telegram",
    "slack": "Told to Ava on Slack",
    "owner_edit": "Written by you",
    "gmail": "Read from an email",
    "calendar": "Read from a calendar event",
}


class ContactEdit(BaseModel):
    """Only what the owner may set. Each field is optional: send the ones that change."""

    model_config = ConfigDict(extra="forbid")
    relationship: str | None = Field(default=None, min_length=1, max_length=30)
    vip: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


def audience_label(item: MemoryItem, owner_id: uuid.UUID, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    """Who may be told this fact, in words, decided from the row's own audience (never from its text)."""
    if item.allowed_kind == "public":
        return {"kind": "anyone", "label": "Anyone, including people Ava does not know"}
    if item.allowed_kind == "org":
        return {"kind": "team", "label": "Your colleagues"}
    allowed = set(item.allowed_person_ids or [])
    if allowed <= {owner_id}:
        return {"kind": "owner", "label": "Only you"}
    who = sorted(names.get(p, "someone") for p in allowed - {owner_id})
    return {"kind": "people", "label": "You and " + ", ".join(who)}


def _owned_contact(s: Session, owner_id: uuid.UUID, contact_id: uuid.UUID) -> tuple[Contact, Person]:
    contact = s.get(Contact, contact_id)
    person = s.get(Person, contact.person_id) if contact is not None else None
    if contact is None or person is None or contact.owner_person_id != owner_id or contact.removed_at is not None:
        raise HTTPException(404, "Not found")
    return contact, person


def _facts_about(s: Session, person_id: uuid.UUID) -> list[MemoryItem]:
    return list(
        s.scalars(
            select(MemoryItem)
            .where(
                or_(MemoryItem.subject_person_ids.contains([person_id]), MemoryItem.author_person_id == person_id),
                MemoryItem.valid_to.is_(None),
            )
            .order_by(MemoryItem.created_at.desc(), MemoryItem.id)
            .limit(MAX_FACTS)
        )
    )


def record(s: Session, owner_id: uuid.UUID, contact: Contact, person: Person) -> dict[str, Any]:
    facts = _facts_about(s, person.id)
    ids: set[uuid.UUID] = {p for f in facts for p in (f.allowed_person_ids or [])}
    names = {p.id: p.display_name for p in s.scalars(select(Person).where(Person.id.in_(ids)))} if ids else {}
    docs = {f.document_id for f in facts if f.document_id}
    titles = (
        {d.id: d.title or d.source for d in s.scalars(select(Document).where(Document.id.in_(docs)))} if docs else {}
    )
    channels = [
        {
            "provider": i.provider,
            "handle": str((i.aux_handles or {}).get("username") or ""),
            "last_seen_at": i.last_seen_at.isoformat() if i.last_seen_at else None,
        }
        for i in s.scalars(
            select(ChannelIdentity)
            .where(ChannelIdentity.person_id == person.id, ChannelIdentity.lifecycle == "active")
            .order_by(ChannelIdentity.provider, ChannelIdentity.id)
        )
    ]
    return {
        "id": str(contact.id),
        "name": person.display_name,
        "relationship": contact.relationship,
        "vip": contact.vip,
        "notes": contact.notes,
        "channels": channels,
        "facts": [
            {
                "id": str(f.id),
                "content": f.content,
                "category": f.category,
                "about_them": person.id in (f.subject_person_ids or []),
                "source": f.source,
                "source_label": SOURCE_LABELS.get(f.source, f.source),
                "source_title": titles.get(f.document_id) if f.document_id else None,
                "learned_at": f.created_at.isoformat(),
                "confidence": f.confidence,
                "audience": audience_label(f, owner_id, names),
            }
            for f in facts
        ],
    }


def make_router() -> APIRouter:
    router = APIRouter(
        prefix="/app/people",
        tags=["Owner: people & rules"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "/{contact_id}",
        summary="One person's record",
        description="What Ava knows about a person on the owner's People list: the owner's fields, where Ava "
        "recognises them, and every live fact with where it came from, when, and who may be told it.",
    )
    def get_record(contact_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            contact, person = _owned_contact(s, person_id, contact_id)
            return record(s, person_id, contact, person)

    @router.patch(
        "/{contact_id}",
        summary="Edit a person's record",
        description="Changes the relationship, the VIP switch or the owner's private notes. Only the fields sent.",
    )
    def edit_record(
        contact_id: uuid.UUID, body: ContactEdit, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            contact, person = _owned_contact(s, person_id, contact_id)
            changed: list[str] = []
            if body.relationship is not None:
                value = " ".join(body.relationship.split())
                if not value:
                    raise HTTPException(422, "The relationship cannot be empty.")
                contact.relationship = value
                changed.append("relationship")
            if body.vip is not None:
                contact.vip = body.vip
                changed.append("vip")
            if body.notes is not None:
                contact.notes = body.notes.strip()
                changed.append("notes")
            audit(s, "app", "contact_edited", contact_id=contact.id, fields=changed)
            s.flush()
            return record(s, person_id, contact, person)

    @router.post(
        "/{contact_id}/facts/{memory_id}/forget",
        summary="Forget a fact about a person",
        description="Ava stops using this fact. The row is kept (retired), not deleted: it no longer reaches any "
        "conversation. Only a fact about this person, or one they told Ava.",
    )
    def forget_fact(
        contact_id: uuid.UUID, memory_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, str]:
        with session_scope() as s:
            _, person = _owned_contact(s, person_id, contact_id)
            m = s.get(MemoryItem, memory_id)
            if (
                m is None
                or m.valid_to is not None
                or (person.id not in (m.subject_person_ids or []) and m.author_person_id != person.id)
            ):
                raise HTTPException(404, "Not found")
            m.valid_to = datetime.now(UTC)
            audit(s, "app", "contact_fact_forgotten", contact_id=contact_id, memory_id=m.id)
        return {"status": "forgotten"}

    return router
