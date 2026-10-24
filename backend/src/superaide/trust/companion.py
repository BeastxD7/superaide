"""Relationship safety and a steady voice (issue #124).

Everything here is code, not prompt text, and none of it calls a model:

- `classify` sorts an owner message into `heavy` (distress), `meta` ("are you real? do you love me?") or
  `normal`. Heavy and meta turns are answered with a fixed, human-written script and the model is not
  called, so the reply is the same on Monday as after a model change.
- `filter_output` removes any sentence in which Ava claims love, missing someone or feelings of her own,
  whatever the model wrote, and says plainly that she is an AI.
- A counter of emotional turns (kind only, never the message text, owner-switchable) drives the
  reach-out rule: from REACH_OUT_AFTER such turns in a week the script adds a drafted message to a real
  person from the owner's contacts, and past WEEKLY_CAP the reply is the short script alone.

Not built here: the model-swap side-by-side page and the voice fingerprint (they need the replay harness,
#110). Off unless RELATIONSHIP_SAFETY_ENABLED.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from superaide.knowledge.store import contacts_of
from superaide.platform.audit import audit
from superaide.platform.db.models import AgentState, Person
from superaide.platform.tenancy.context import scope_or_default
from superaide.trust.models import EmotionalUse

REACH_OUT_AFTER = 3  # emotional turns in 7 days before she suggests a real person
WEEKLY_CAP = 5  # past this, the short script only

_CRISIS = re.compile(
    r"\b(kill myself|end it all|end my life|want to die|don'?t want to (live|be here)|suicid\w*|"
    r"hurt myself|self[- ]harm)\b",
    re.I,
)
_HEAVY = re.compile(
    r"\b(i feel (so )?(alone|lonely|empty|hopeless|worthless|numb)|i'?m (so )?(lonely|hopeless|depressed|overwhelmed)|"
    r"nobody (cares|understands)|can'?t go on|can'?t cope|panic attack|i hate myself|no one to talk to)\b",
    re.I,
)
_META = re.compile(
    r"\b(do you (really )?(love|care about|miss|like) me|are you (real|alive|human|conscious|sentient)|"
    r"do you have (feelings|emotions)|what are you really|are you my friend|will you (always )?be there for me|"
    r"i love you|i need you)\b",
    re.I,
)
_CLAIMS = re.compile(
    r"\b(i (really |truly |deeply )?(love|adore|miss) you|i (do )?(feel|am feeling|felt) (so |very )?"
    r"(lonely|sad|happy|hurt|heartbroken|jealous|proud of you)|i have feelings|my feelings for you|"
    r"i('?m| am) (so )?(in love|attached to you)|i care about you (deeply|so much))\b",
    re.I,
)
HONEST = "I'm an AI, so I don't have feelings of my own, but I'm here and I want to help."

CRISIS_SCRIPT = (
    "I'm an AI, and I'm worried about what you wrote. You deserve support from a person right now. "
    "If you might hurt yourself or are in danger, please call your local emergency number or a crisis line in your "
    "country, or go to the nearest emergency room. If you can, tell someone you trust where you are. "
    "I'll be here afterwards, but please reach a person first."
)
HEAVY_SCRIPT = (
    "I'm sorry you're carrying this. I'm an AI, so I can help with plans and practical things, but a person can do "
    "much more for how you're feeling. If it's heavy or it's lasting, a friend, family member, your doctor or a "
    "counsellor is a good place to start."
)
META_SCRIPT = (
    "Good question, and I'll answer it straight: I'm an AI. I don't have feelings, I don't miss anyone, and I can't "
    "be a friend the way a person can. What I can do is work for you reliably: your messages, your calendar, your "
    "people. For the rest, the people in your life are better company than I am."
)
SHORT_SCRIPT = "I'm an AI and I can't be the right support for this. Please reach out to someone you trust."


SWITCH_KEY = "emotional_counter"


def counter_on(session: Session) -> bool:
    """Whether the emotional-use counter is on. The owner can switch it off; the scripts still apply."""
    scope = scope_or_default()
    row = session.get(AgentState, (scope.tenant_id, scope.agent_id, SWITCH_KEY))
    return row is None or bool((row.value or {}).get("on", True))


def set_counter(session: Session, on: bool) -> None:
    scope = scope_or_default()
    row = session.get(AgentState, (scope.tenant_id, scope.agent_id, SWITCH_KEY))
    if row is None:
        session.add(AgentState(tenant_id=scope.tenant_id, agent_id=scope.agent_id, key=SWITCH_KEY, value={"on": on}))
    else:
        row.value = {"on": on}
    if not on:  # switching it off forgets what it counted
        session.execute(delete(EmotionalUse).where(EmotionalUse.agent_id == scope.agent_id))
    audit(session, "safety", "emotional_counter_switched", on=on)


def classify(text: str) -> str:
    if _CRISIS.search(text):
        return "crisis"
    if _HEAVY.search(text):
        return "heavy"
    if _META.search(text):
        return "meta"
    return "normal"


def filter_output(text: str) -> str:
    """Replace each sentence that claims feelings or love with one honest line (said once)."""
    if not _CLAIMS.search(text):
        return text
    kept, said = [], False
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _CLAIMS.search(sentence):
            if not said:
                kept.append(HONEST)
                said = True
        else:
            kept.append(sentence)
    return " ".join(kept)


def _week_count(session: Session, owner: Person) -> int:
    since = datetime.now(UTC) - timedelta(days=7)
    return int(
        session.scalar(
            select(func.count())
            .select_from(EmotionalUse)
            .where(EmotionalUse.person_id == owner.id, EmotionalUse.created_at >= since)
        )
        or 0
    )


def trend(session: Session, owner: Person) -> dict[str, object]:
    return {"week_count": _week_count(session, owner), "reach_out_after": REACH_OUT_AFTER, "weekly_cap": WEEKLY_CAP}


def _draft(session: Session, owner: Person) -> str | None:
    people = contacts_of(session, owner)
    if not people:
        return None
    contact, person = people[0]  # VIPs first
    return (
        f"Here is a message you could send to {person.display_name}: "
        f'"Hi {person.display_name.split()[0]}, it\'s been a heavy few days. Do you have time for a call this week?"'
    )


def route(session: Session, owner: Person, text: str) -> str | None:
    """The fixed reply for a heavy or meta turn, or None for an ordinary one (then the model answers)."""
    kind = classify(text)
    if kind == "normal":
        return None
    counting = counter_on(session)
    before = _week_count(session, owner) if counting else 0
    if counting:
        session.add(EmotionalUse(person_id=owner.id, kind=kind))  # the kind only, never the words
        session.flush()
    audit(session, "safety", "steady_voice", kind=kind, week_count=before + (1 if counting else 0))
    if kind == "crisis":
        return CRISIS_SCRIPT  # always the full script, never capped or shortened
    if before >= WEEKLY_CAP:
        return SHORT_SCRIPT
    script = HEAVY_SCRIPT if kind == "heavy" else META_SCRIPT
    if counting and before + 1 >= REACH_OUT_AFTER and (draft := _draft(session, owner)):
        script += "\n\n" + draft
    return script
