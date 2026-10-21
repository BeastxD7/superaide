"""Episodic log and simple audience-tagged memories (phase 1)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo

from sqlalchemy import or_, select
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
    external_id: str = "",
    thread_id: str = "",
) -> Event:
    event = Event(
        external_id=external_id or None,
        thread_id=thread_id or None,
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


def seen_before(session: Session, context: Context, external_id: str) -> bool:
    """Whether this inbound message was already logged (a platform redelivery)."""
    if not external_id:
        return False
    return (
        session.scalar(
            select(Event.id).where(
                Event.context_id == context.id, Event.direction == "in", Event.external_id == external_id
            )
        )
        is not None
    )


def recent_events(session: Session, context: Context, limit: int = 12, thread_id: str = "") -> list[Event]:
    """The latest messages of this conversation: one thread, or the main conversation outside threads."""
    in_thread = (
        or_(Event.thread_id == thread_id, Event.external_id == thread_id)  # the thread's first message
        if thread_id
        else Event.thread_id.is_(None)
    )
    rows = session.scalars(
        select(Event)
        .where(Event.context_id == context.id, in_thread)
        .order_by(Event.created_at.desc(), Event.id)
        .limit(limit)
    ).all()
    return list(reversed(rows))


# --- one conversation across channels (ADR 0008) ---------------------------------------

TIMELINE_WINDOW = timedelta(hours=48)
TIMELINE_OTHER = 12  # messages from the owner's other channels
TIMELINE_CHARS = 8_000  # about 2,000 tokens for the whole history
TIMELINE_EVENT_CHARS = 600
WHERE = {"telegram": "On Telegram", "slack": "On Slack", "web": "In the app"}
LABEL_RE = re.compile(r"^\s*\[(?:On|In) [^\]\n]{1,60}\]\s*")


@dataclass
class Timeline:
    events: list[Event]
    labels: dict[uuid.UUID, str] = field(default_factory=dict)  # event id → "[On Telegram · Wed 19:13]"


def owner_timeline(
    session: Session,
    owner_id: uuid.UUID,
    context: Context,
    thread_id: str = "",
    tz: tzinfo = UTC,
    now: datetime | None = None,
) -> Timeline:
    """The owner's recent conversation on every channel, merged by time: this conversation's
    latest messages, plus what they and Ava said in their other private DMs in the last 48
    hours, each labelled with where it happened. Call only for the owner's own private DM.

    Only messages whose audience was exactly the owner, with nobody unknown present, in a DM,
    qualify: groups, channels, threads with others and other people's DMs never do."""
    now = now or datetime.now(UTC)
    here = recent_events(session, context, thread_id=thread_id)
    rows = session.execute(
        select(Event, Context.provider)
        .join(Context, Context.id == Event.context_id)
        .where(
            Event.context_id != context.id,
            Event.agent_id == context.agent_id,  # this Ava instance only (ADR 0009 tenancy)
            Context.kind == "dm",
            Event.audience_person_ids == [owner_id],
            Event.audience_has_unknown.is_(False),
            or_(Event.resolved_person_id == owner_id, Event.direction == "out"),
            Event.content != "",
            Event.created_at > now - TIMELINE_WINDOW,
        )
        .order_by(Event.created_at.desc())
        .limit(TIMELINE_OTHER)
    ).all()
    labels: dict[uuid.UUID, str] = {}
    for event, provider in rows:
        when = event.created_at.astimezone(tz)
        labels[event.id] = f"[{WHERE.get(provider, 'On ' + provider.title())} · {when:%a %H:%M}]"
    # This conversation is kept whole, as before; other channels fill what's left of the
    # budget, newest first.
    room = TIMELINE_CHARS - sum(len(e.content) for e in here)
    others: list[Event] = []
    for event, _ in rows:
        size = min(len(event.content), TIMELINE_EVENT_CHARS) + len(labels[event.id])
        if size > room:
            break
        others.append(event)
        room -= size
    kept = sorted([*here, *others], key=lambda e: e.created_at)
    return Timeline(kept, {e.id: labels[e.id] for e in kept if e.id in labels})


def strip_label(text: str) -> str:
    """Ava must not write the app's channel labels herself; remove one she copied."""
    return LABEL_RE.sub("", text, count=1)


def parse_remember(text: str) -> str | None:
    m = REMEMBER_RE.match(text)
    if not m:
        return None
    fact = m.group(1).strip()
    # "Remember that" / "remember this" with nothing after it is not a fact.
    return None if fact.lower().rstrip(".!:,") in {"", "that", "this", "it"} else fact


def _same_fact(text: str) -> str:
    from superaide.context.lexicon import normalise  # one normaliser for every fact writer

    return normalise(text)


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
