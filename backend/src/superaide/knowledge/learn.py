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
from types import SimpleNamespace

from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Event, Job, Person
from superaide.jobs.queue import enqueue, handler
from superaide.knowledge.facts import extract_facts
from superaide.knowledge.store import remember_fact

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
    facts = extract_facts(
        agent,
        owner_name=owner.display_name,
        text=event.content,
        source="the owner, in a chat message to Ava",
        today=f"{local:%A %d %B %Y}",
    )
    if facts is None:
        raise RuntimeError("couldn't read the message just now")  # the job retries later
    kept = 0
    for f in facts:
        item = remember_fact(
            session, owner=owner, content=f.content, category=f.category, source="chat",
            confidence=f.confidence, origin_context_id=event.context_id,
        )  # fmt: skip
        if item is not None:
            item.source_event_ids = [event.id]
            kept += 1
    audit(session, "memory", "learned_from_chat", event_id=event.id, facts=kept)
