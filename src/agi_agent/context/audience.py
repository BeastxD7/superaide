"""Audience gate: decides which memories may appear in a conversation.

The rule runs inside the SQL query, before the model sees anything:
- A memory learned in a private chat is visible only when everyone present was
  in its allowed audience.
- Any unknown participant (a stranger, a guest, an unlisted group member)
  shrinks what is visible to memories marked public.
- Group chats count as having unknown members until membership is verified,
  so in phase 1 they only ever see public memories.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from agi_agent.db.models import Context, MemoryItem
from agi_agent.identity.resolver import Sender


@dataclass
class Audience:
    person_ids: set[uuid.UUID] = field(default_factory=set)
    has_unknown: bool = False
    min_trust: int = 0
    all_internal: bool = True

    def describe(self) -> str:
        if self.has_unknown:
            return "a conversation that includes people you cannot verify"
        if len(self.person_ids) == 1:
            return "a private one-to-one conversation"
        return f"a conversation with {len(self.person_ids)} verified people"


def audience_for(context: Context, sender: Sender) -> Audience:
    if context.kind == "dm" and sender.known:
        assert sender.person is not None
        return Audience(
            person_ids={sender.person.id},
            has_unknown=False,
            min_trust=sender.trust,
            all_internal=sender.relation in ("manager", "colleague"),
        )
    # Strangers, external people and group chats: treat as unknown audience.
    ids = {sender.person.id} if sender.person else set()
    return Audience(person_ids=ids, has_unknown=True, min_trust=0, all_internal=False)


def memory_filter(aud: Audience, now: datetime | None = None) -> ColumnElement[bool]:
    now = now or datetime.now(UTC)
    public = MemoryItem.allowed_kind == "public"
    org = and_(
        MemoryItem.allowed_kind == "org",
        # Python-side facts about the audience become constant SQL booleans.
        _const(not aud.has_unknown and aud.all_internal),
    )
    persons = and_(
        MemoryItem.allowed_kind == "persons",
        _const(not aud.has_unknown and bool(aud.person_ids)),
        MemoryItem.allowed_person_ids.contains(sorted(aud.person_ids)),  # allowed ⊇ audience
    )
    return and_(
        or_(public, org, persons),
        MemoryItem.min_trust <= aud.min_trust if not aud.has_unknown else MemoryItem.allowed_kind == "public",
        or_(MemoryItem.valid_to.is_(None), MemoryItem.valid_to > now),
    )


def _const(value: bool) -> ColumnElement[bool]:
    from sqlalchemy import false, true

    return true() if value else false()


def visible_memories(session: Session, aud: Audience, limit: int = 5) -> list[MemoryItem]:
    """At most `limit` memories, newest first. Few results limit memory poisoning."""
    return list(
        session.scalars(
            select(MemoryItem).where(memory_filter(aud)).order_by(MemoryItem.created_at.desc()).limit(limit)
        ).all()
    )
