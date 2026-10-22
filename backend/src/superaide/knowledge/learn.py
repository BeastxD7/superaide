"""Learning from conversation: after the owner says something, keep what's worth knowing.

Runs as a background job after the reply (no wait for the owner, about US$0.0005 a
message on Haiku, from the memory budget). For now it learns only from the owner's own
messages: third-party messages are where memory poisoning comes from, and they need the
candidate/verify tier the research describes before they can teach Ava anything.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.brain import keyhealth
from superaide.db.models import Context, Event, Job, MemoryItem, Person
from superaide.jobs.queue import enqueue, handler
from superaide.knowledge.facts import extract_facts
from superaide.knowledge.store import channel_source, find_same_fact, remember_fact

log = logging.getLogger(__name__)

JOB_KIND = "learn_from_turn"
MIN_CHARS = 25  # "ok", "thanks", "what's on today?" teach nothing


# A short first-person stance is worth reading however short: "I hate black.", "I'm vegetarian",
# "no beige, ever", "call me Shash", "my daughter is Asha".
STANCE = re.compile(
    r"^\s*(?:(?:btw|fyi|also|and|oh|honestly|actually)[,\s]+)*"
    r"(?:i\s+(?:(?:really|do|don'?t|do\s+not|never|always|can'?t|cannot|absolutely|kind\s+of|kinda)\s+)*"
    r"(?:like|love|hate|dislike|prefer|enjoy|stand|avoid|eat|drink|wear|use|want|need|work|live|am|have|go)\b"
    r"|i'?m\s+\w+|i\s+am\s+\w+|my\s+\w+(?:'s)?\s+(?:is|are|was)\b|call\s+me\b|no\s+\w+.*\b(?:ever|please|thanks)\b"
    r")",
    re.I,
)
SMALL_TALK = re.compile(r"^\s*i(?:'?m|\s+am)\s+(?:fine|ok|okay|good|great|here|back|done|in|on|sure|busy|home)\b", re.I)


def worth_reading(text: str) -> bool:
    from superaide.intentions.when import is_reminder_request
    from superaide.memory.store import parse_remember

    t = text.strip()
    if t.endswith("?") and len(t) < 80:
        return False  # short questions are requests, not facts
    if len(t) < MIN_CHARS and (not STANCE.match(t) or SMALL_TALK.match(t)):
        return False  # "ok", "thanks", "sounds good" teach nothing
    return parse_remember(t) is None and not is_reminder_request(t)  # those are stored already


def schedule(session: Session, event: Event) -> None:
    """Queue learning from one inbound owner message (once per message)."""
    if worth_reading(event.content):
        enqueue(session, JOB_KIND, {"event_id": str(event.id)}, dedupe_key=f"learn:{event.id}", max_attempts=3)


@handler(JOB_KIND)
def learn(session: Session, job: Job) -> None:
    from superaide.factory import brain_ready, build_llm
    from superaide.vault.store import runtime_settings

    event = session.get(Event, uuid.UUID(job.payload["event_id"]))
    if event is None or event.resolved_person_id is None:
        return
    owner = session.get(Person, event.resolved_person_id)
    if owner is None:
        return
    settings = runtime_settings()
    if not brain_ready(settings):
        return  # offline: nothing can read it; the message itself stays in the log
    agent = SimpleNamespace(settings=settings, llm=build_llm(settings))
    from superaide.intentions.service import owner_zone
    from superaide.intentions.when import zone

    local = event.created_at.astimezone(zone(owner_zone(session, owner)))
    neighbours = _neighbours(session, owner, event.content)
    facts = extract_facts(
        agent,
        owner_name=owner.display_name,
        text=event.content,
        source="the owner, in a chat message to Ava",
        today=f"{local:%A %d %B %Y}",
        known=[m.content for m in neighbours],
    )
    if facts is None:
        if keyhealth.rejected(session):
            return  # a refused key won't heal by waiting; a working one is saved from the AI model page
        raise RuntimeError("couldn't read the message just now")  # the job retries later
    context = session.get(Context, event.context_id)
    source = channel_source(context.provider if context else None)
    by_id = dict(enumerate(neighbours, 1))
    kept = merged = superseded = 0
    for f in facts:
        same = by_id.get(f.same_as or 0) or find_same_fact(session, owner, f.content)
        if same is not None and _owner_only(same, owner):
            if event.id not in (same.source_event_ids or []):  # said again: one fact, both sources
                same.source_event_ids = [*(same.source_event_ids or []), event.id]
            if same.document_id is not None:
                # The owner has now said it themselves. Taking the file away must not retire what
                # they confirmed, so the fact no longer belongs to the document.
                same.document_id = None
            merged += 1
            continue
        item = remember_fact(
            session, owner=owner, content=f.content, category=f.category, source=source,
            confidence=f.confidence, origin_context_id=event.context_id,
        )  # fmt: skip
        if item is None:
            continue
        item.source_event_ids = [event.id]
        kept += 1
        old = by_id.get(f.replaces or 0)
        if old is not None and old.valid_to is None:
            old.valid_to = datetime.now(UTC)  # the owner changed their mind: the old fact retires
            superseded += 1
            audit(session, "memory", "superseded", memory_id=old.id, by=item.id)
    audit(session, "memory", "learned_from_chat", event_id=event.id, facts=kept, merged=merged, superseded=superseded)


def _owner_only(item: MemoryItem, owner: Person) -> bool:
    """A learned fact is owner-only; it may merge only into a fact with that same audience, so
    a merge never widens (or narrows) who can see what."""
    return item.allowed_kind == "persons" and list(item.allowed_person_ids or []) == [owner.id]


def _neighbours(session: Session, owner: Person, text: str, limit: int = 5) -> list[MemoryItem]:
    """The owner's known facts closest to this message, for the extractor to compare against."""
    from superaide.context.audience import Audience
    from superaide.context.recall import recall_memories

    found = recall_memories(session, Audience(person_ids={owner.id}, min_trust=2), text, limit=limit)
    ids = [uuid.UUID(src.uri.rsplit("/", 1)[1]) for src in found]
    rows = {m.id: m for m in session.scalars(select(MemoryItem).where(MemoryItem.id.in_(ids)))} if ids else {}
    return [rows[i] for i in ids if i in rows and rows[i].agent_id == owner.agent_id]
