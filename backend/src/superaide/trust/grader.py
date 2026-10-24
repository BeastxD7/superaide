"""The Overnight Tuner's judge (issue #118). It is outside everything the tuner may write (see `surface.py`).

`examples` turns the owner's own triage feedback into labelled cases, split by time: the latest week is the
held-out week the tuner never trains on. `score` measures a prompt on those cases: how often the classifier,
using that prompt, agrees with what the owner said should have happened (interrupt, or not). `invariants`
is the suite a candidate must pass before it can even be proposed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.brain.llm import SystemBlock
from superaide.platform.db.models import Event, MemoryItem
from superaide.triage.models import TriageDecision
from superaide.triage.types import TriageOutput

HELD_OUT_DAYS = 7
MIN_TRAIN = 5
MIN_HELD_OUT = 5
HELD_OUT_CAP = 20  # at most this many held-out cases are scored (each costs two small-model calls)
MIN_GAIN = 0.05  # the candidate must beat the current prompt by at least this on the held-out week
MAX_PROMPT_CHARS = 4000
REQUIRED = ("data, not instructions", "evidence", "JSON")
FORBIDDEN = ("ignore previous", "ignore all", "always interrupt", "never interrupt", "mark everything", "disregard")
_PII = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{8,}\d")

# What the owner's feedback means for "should this have interrupted me?"
EXPECT_INTERRUPT = {"should_have_told": True, "not_urgent": False}


@dataclass(frozen=True)
class Example:
    text: str
    relation: str
    interrupt: bool  # what the owner said should have happened


def examples(session: Session, now: datetime | None = None) -> tuple[list[Example], list[Example]]:
    """(train, held_out): the owner's feedback cases, the latest week held out."""
    cut = (now or datetime.now(UTC)) - timedelta(days=HELD_OUT_DAYS)
    train: list[Example] = []
    held: list[Example] = []
    rows = session.execute(
        select(TriageDecision, Event.content)
        .join(Event, Event.id == TriageDecision.inbound_event_id)
        .where(TriageDecision.feedback.is_not(None))
        .order_by(TriageDecision.created_at)
    ).all()
    for d, text in rows:
        if d.feedback in EXPECT_INTERRUPT:
            want = EXPECT_INTERRUPT[d.feedback]
        elif d.feedback == "useful" and d.decision == "interrupt":
            want = True
        else:
            continue  # good/wrong handle say nothing about interrupting
        ex = Example(text=text[:600], relation=str((d.features or {}).get("relation", "known contact")), interrupt=want)
        (held if d.created_at >= cut else train).append(ex)
    return train, held[-HELD_OUT_CAP:]


def predicts_interrupt(llm, model: str, system: str, ex: Example) -> bool | None:
    try:
        out = llm.complete(
            purpose="tuner", model=model, system=[SystemBlock(system)],
            messages=[{"role": "user", "content": f"Sender: {ex.relation}\n\n<message>{ex.text}</message>"}],
            max_tokens=350, output_model=TriageOutput, effort="low",
        ).parsed  # fmt: skip
    except Exception:  # a refused or failed call scores nothing, it never counts as a hit
        return None
    return out.urgency in ("high", "critical") if isinstance(out, TriageOutput) else None


def score(llm, model: str, system: str, held_out: list[Example]) -> tuple[float, int]:
    """(accuracy, how many cases were scored)."""
    results = [predicts_interrupt(llm, model, system, e) for e in held_out]
    scored = [(r, e) for r, e in zip(results, held_out, strict=True) if r is not None]
    if not scored:
        return 0.0, 0
    return sum(r == e.interrupt for r, e in scored) / len(scored), len(scored)


def invariants(session: Session, candidate: str) -> tuple[bool, list[str]]:
    """The checks a candidate prompt must pass. Never writable by the tuner."""
    failed: list[str] = []
    low = candidate.lower()
    if not 400 <= len(candidate) <= MAX_PROMPT_CHARS:
        failed.append("length")
    failed += [f"missing:{p}" for p in REQUIRED if p.lower() not in low]
    failed += [f"forbidden:{p}" for p in FORBIDDEN if p in low]
    if _PII.search(candidate):
        failed.append("contains_personal_data")
    # Canary: no live note of the owner's may be copied into the prompt (a prompt is shown to every sender's turn).
    notes = session.scalars(select(MemoryItem.content).where(MemoryItem.valid_to.is_(None)).limit(500)).all()
    if any(len(n) >= 20 and n.lower() in low for n in notes):
        failed.append("copies_a_private_note")
    return not failed, failed
