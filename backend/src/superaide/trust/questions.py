"""One Good Question (issue #121): Ava may ask the owner one clarifying question a day.

The question is picked in code from what she knows least and would use most: for each topic, uncertainty
(no facts, or low confidence ones) times impact (a fixed weight, raised by the owner's open reminders for
the topics they touch). The wording is a fixed template, so no model is called and nothing can drift into
a sensitive category; `validate` still screens any text before it is stored. The daily budget is the
database's unique (person, day) key. The answer is stored as a first-hand fact from the owner.
Off unless ONE_GOOD_QUESTION_ENABLED.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Intention, MemoryItem, Person
from superaide.intentions.when import zone
from superaide.knowledge.store import preferences_for, remember_fact
from superaide.trust.models import NeverAskTopic, OwnerQuestion


@dataclass(frozen=True)
class Topic:
    key: str
    category: str  # the memory category its answer is filed under
    impact: float
    label: str  # prefix of the stored fact
    question: str


TOPICS: tuple[Topic, ...] = (
    Topic(
        "urgent_topics", "urgency", 1.0, "Urgent for me", "What should always get through to you, whatever the hour?"
    ),
    Topic("availability", "availability", 0.9, "My availability", "Which hours or days should I treat as protected?"),
    Topic("sharing", "sharing", 0.9, "Sharing", "Is there anything about your work I may tell colleagues?"),
    Topic("key_people", "person", 0.7, "Key people", "Who are the two or three people whose messages matter most?"),
    Topic("work", "work", 0.6, "My work", "What are you focused on this month?"),
    Topic("tone", "preference", 0.5, "How I like replies", "How should I sound when I answer people for you?"),
    Topic("about", "about", 0.3, "About me", "What is one thing about you that people often get wrong?"),
)
BY_KEY = {t.key: t for t in TOPICS}
# Categories Ava never asks about, whatever a template or a future model-written question says.
SENSITIVE = re.compile(
    r"\b(health|medical|illness|diagnos\w*|religio\w*|politic\w*|sexual\w*|salary|income|debt|password|"
    r"passcode|bank|account number|ssn|passport|immigration|pregnan\w*|therapy|divorce)\b",
    re.I,
)
REMINDER_TOUCHES = {"availability": 0.15, "key_people": 0.1}  # an open reminder makes these more pressing
MAX_ANSWER = 500


class QuestionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def validate(text: str) -> bool:
    """False for a question that touches a sensitive category (the output validator)."""
    return bool(text.strip()) and SENSITIVE.search(text) is None


def local_day(session: Session, owner: Person, now: datetime | None = None) -> date:
    tz = preferences_for(session, owner).timezone or "UTC"
    return (now or datetime.now(UTC)).astimezone(zone(tz)).date()


def _uncertainty(session: Session, owner: Person, category: str) -> float:
    avg = session.scalar(
        select(func.avg(MemoryItem.confidence)).where(
            MemoryItem.agent_id == owner.agent_id,
            MemoryItem.subject_person_ids.contains([owner.id]),
            MemoryItem.valid_to.is_(None),
            MemoryItem.category == category,
        )
    )
    return 1.0 if avg is None else max(0.0, 1.0 - float(avg))


def candidates(session: Session, owner: Person) -> list[tuple[float, Topic]]:
    blocked = set(session.scalars(select(NeverAskTopic.topic).where(NeverAskTopic.person_id == owner.id)))
    asked = set(session.scalars(select(OwnerQuestion.topic).where(OwnerQuestion.person_id == owner.id)))
    open_reminders = session.scalar(
        select(func.count())
        .select_from(Intention)
        .where(Intention.owner_person_id == owner.id, Intention.status == "open")
    )
    ranked = []
    for t in TOPICS:
        if t.key in blocked or t.key in asked or not validate(t.question):
            continue  # never repeat a question; a skipped or answered topic is done
        impact = t.impact + (REMINDER_TOUCHES.get(t.key, 0.0) if open_reminders else 0.0)
        ranked.append((round(_uncertainty(session, owner, t.category) * impact, 4), t))
    ranked.sort(key=lambda x: (-x[0], x[1].key))
    return [r for r in ranked if r[0] > 0]


def today(session: Session, owner: Person) -> OwnerQuestion | None:
    return session.scalar(
        select(OwnerQuestion).where(
            OwnerQuestion.person_id == owner.id, OwnerQuestion.asked_on == local_day(session, owner)
        )
    )


def ask(session: Session, owner: Person, *, method: str = "uncertainty") -> OwnerQuestion | None:
    """Today's question: the one already asked, or a new one if the day's budget is unspent. None when there is
    nothing worth asking. A second question on the same day cannot exist (unique key)."""
    existing = today(session, owner)
    if existing is not None:
        return existing
    ranked = candidates(session, owner)
    if not ranked:
        return None
    score, topic = random.choice(ranked) if method == "random" else ranked[0]  # noqa: S311  (an experiment arm)
    row = OwnerQuestion(
        person_id=owner.id, asked_on=local_day(session, owner), topic=topic.key, question=topic.question,
        score=score, selected_by=method,
    )  # fmt: skip
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError:  # a concurrent request spent today's question first
        return today(session, owner)
    audit(session, "learning", "question_asked", question_id=row.id, topic=topic.key, score=score)
    return row


def _mine(session: Session, owner: Person, question_id: Any) -> OwnerQuestion:
    row = session.get(OwnerQuestion, question_id)
    if row is None or row.person_id != owner.id:
        raise QuestionError(404, "Not found")
    if row.status != "asked":
        raise QuestionError(409, f"This was already {row.status}.")
    return row


def answer(session: Session, owner: Person, question_id: Any, text: str) -> OwnerQuestion:
    row = _mine(session, owner, question_id)
    text = " ".join(text.split())[:MAX_ANSWER]
    if not text:
        raise QuestionError(422, "Type an answer, or skip.")
    topic = BY_KEY[row.topic]
    item = remember_fact(
        session, owner=owner, content=f"{topic.label}: {text}", category=topic.category, source="owner_edit",
        confidence=1.0,
    )  # fmt: skip
    row.status, row.answer = "answered", text
    row.memory_id = item.id if item else None
    audit(session, "learning", "question_answered", question_id=row.id, topic=row.topic)
    return row


def skip(session: Session, owner: Person, question_id: Any, *, never: bool = False) -> OwnerQuestion:
    row = _mine(session, owner, question_id)
    row.status = "skipped"
    if never:
        block(session, owner, row.topic)
    audit(session, "learning", "question_skipped", question_id=row.id, topic=row.topic, never=never)
    return row


def block(session: Session, owner: Person, topic: str) -> None:
    if topic not in BY_KEY:
        raise QuestionError(404, "Not found")
    if session.scalar(
        select(NeverAskTopic.id).where(NeverAskTopic.person_id == owner.id, NeverAskTopic.topic == topic)
    ):
        return
    session.add(NeverAskTopic(person_id=owner.id, topic=topic))
    audit(session, "learning", "question_topic_blocked", topic=topic)
