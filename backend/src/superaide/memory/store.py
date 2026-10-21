"""Episodic log and simple audience-tagged memories (phase 1)."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.channels.base import InboundMessage
from superaide.context.audience import Audience
from superaide.db.models import Context, Event, MemoryItem
from superaide.identity.resolver import Sender

REMEMBER_RE = re.compile(r"^\s*remember(?:\s+that)?[:,]?\s+(.+)$", re.IGNORECASE | re.DOTALL)


def get_or_create_context(session: Session, msg: InboundMessage) -> Context:
    ctx = session.scalar(
        select(Context).where(
            Context.provider == msg.provider,
            Context.tenant_scope == ("" if msg.provider == "telegram" else _workspace_scope(msg)),
            Context.external_id == msg.chat_id,
        )
    )
    if ctx is None:
        if msg.chat_kind == "dm":
            audience_class = "mixed_external" if msg.external_sender else "private_1to1"
        else:
            audience_class = "mixed_external" if msg.external_sender else "team"
        ctx = Context(
            provider=msg.provider,
            tenant_scope="" if msg.provider == "telegram" else _workspace_scope(msg),
            external_id=msg.chat_id,
            kind=msg.chat_kind,
            audience_class=audience_class,
            title=msg.chat_title,
        )
        session.add(ctx)
        session.flush()
    elif msg.external_sender and ctx.audience_class != "mixed_external":
        ctx.audience_class = "mixed_external"
    return ctx


def _workspace_scope(msg: InboundMessage) -> str:
    # Conversations live in the workspace that received the event.
    return str(msg.raw.get("team_id") or msg.tenant_scope or "")


def log_event(
    session: Session,
    *,
    context: Context,
    direction: str,
    content: str,
    audience: Audience,
    sender: Sender | None = None,
    verified: bool = True,
    meta: dict | None = None,
) -> Event:
    event = Event(
        context_id=context.id,
        direction=direction,
        sender_identity_id=sender.identity.id if sender else None,
        resolved_person_id=sender.person.id if sender and sender.person else None,
        trust_at_time=sender.trust if sender else 0,
        audience_person_ids=sorted(audience.person_ids),
        audience_has_unknown=audience.has_unknown,
        content=content,
        meta=meta or {},
        webhook_verified=verified,
    )
    session.add(event)
    session.flush()
    return event


def recent_events(session: Session, context: Context, limit: int = 12) -> list[Event]:
    rows = session.scalars(
        select(Event).where(Event.context_id == context.id).order_by(Event.created_at.desc(), Event.id).limit(limit)
    ).all()
    return list(reversed(rows))


def parse_remember(text: str) -> str | None:
    m = REMEMBER_RE.match(text)
    if not m:
        return None
    fact = m.group(1).strip()
    # "Remember that" / "remember this" with nothing after it is not a fact.
    return None if fact.lower().rstrip(".!:,") in {"", "that", "this", "it"} else fact


def _same_fact(text: str) -> str:
    return " ".join(text.lower().split()).rstrip(".!")


def remember(
    session: Session, *, content: str, sender: Sender, context: Context, audience: Audience, source_event: Event
) -> MemoryItem:
    """Store a fact visible only to the audience it was told in. Saying it again keeps one note."""
    assert sender.person is not None
    audience_ids = sorted(audience.person_ids)
    for existing in session.scalars(
        select(MemoryItem).where(
            MemoryItem.valid_to.is_(None),
            MemoryItem.subject_person_ids.contains([sender.person.id]),
            MemoryItem.allowed_kind == "persons",
            MemoryItem.allowed_person_ids == audience_ids,
        )
    ):
        if _same_fact(existing.content) == _same_fact(content):
            existing.source_event_ids = [*(existing.source_event_ids or []), source_event.id]
            audit(session, "memory", "remembered_again", memory_id=existing.id)
            return existing
    item = MemoryItem(
        content=content,
        subject_person_ids=[sender.person.id],
        source_event_ids=[source_event.id],
        origin_context_id=context.id,
        allowed_kind="persons",
        allowed_person_ids=audience_ids,
        min_trust=2,
    )
    session.add(item)
    session.flush()
    audit(session, "memory", "remembered", memory_id=item.id, audience=[str(i) for i in audience.person_ids])
    return item


def as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
