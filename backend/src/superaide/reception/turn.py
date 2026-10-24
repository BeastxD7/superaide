"""One receptionist turn: a strict structured answer from the cheap model, grounded only in what the
owner made public (ADR 0010 TR5).

What reaches the prompt, and nothing else:
- the persona (brain/receptionist.md), with the owner's first name only if they allowed it;
- the owner's public profile text;
- memories the owner marked public, for this instance only (the audience gate, in SQL);
- what the owner may share, as labels (the values must come from the two above);
- one sentence about being away, only when the owner lets everyone know;
- this stranger's own conversation in this chat, and nothing from any other.

It is never given the owner brief (that tells the model the owner's local time and quiet hours),
private memories, other conversations, tools, or anything it could act with. Its only output
besides the reply is `take_message`, which code stores as data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.brain.providers.types import SystemBlock
from superaide.context.audience import Audience, memory_filter
from superaide.platform.db.models import MemoryItem, OwnerPreference, Person
from superaide.reception.models import ReceptionSettings

PERSONA = Path(__file__).resolve().parents[1] / "brain" / "receptionist.md"
PURPOSE = "receptionist"  # brain/budget.py BUCKETS: its own daily bucket
MAX_TOKENS = 400
EFFORT = "low"  # Claude Haiku 5.5 thinks by default and thinking counts against max_tokens
PUBLIC_FACTS = 12
HISTORY = 12  # turns of this chat


class TakenMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    contact: str = ""
    topic: str = ""
    urgency: Literal["low", "normal", "high", "critical"] = "normal"
    deadline_text: str = ""
    message: str = ""


class ReceptionTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    take_message: TakenMessage | None = None


def first_name(owner: Person) -> str:
    return (owner.display_name or "").split()[0] if (owner.display_name or "").split() else ""


def public_facts(session: Session, owner: Person) -> list[str]:
    """Memories the owner marked public, for this instance only. The audience gate runs in SQL."""
    stranger = Audience(person_ids=set(), has_unknown=True, min_trust=0, all_internal=False)
    rows = session.scalars(
        select(MemoryItem.content)
        .where(memory_filter(stranger), MemoryItem.agent_id == owner.agent_id)
        .order_by(MemoryItem.created_at.desc())
        .limit(PUBLIC_FACTS)
    ).all()
    return [r for r in rows if r]


def system_prompt(session: Session, owner: Person, rs: ReceptionSettings, agent_name: str) -> list[SystemBlock]:
    from superaide.knowledge.profile import SHARE_LABELS

    owner_ref = f"{first_name(owner)}" if rs.show_first_name and first_name(owner) else "the person you work for"
    persona = PERSONA.read_text(encoding="utf-8").replace("{agent_name}", agent_name).replace("{owner_ref}", owner_ref)
    parts = ["Public information (all you may share):"]
    if rs.public_profile.strip():
        parts.append(f"From their public profile:\n{rs.public_profile.strip()[:2000]}")
    facts = public_facts(session, owner)
    if facts:
        parts.append("Public notes:\n- " + "\n- ".join(f[:300] for f in facts))
    pref = session.scalar(select(OwnerPreference).where(OwnerPreference.person_id == owner.id))
    labels = [SHARE_LABELS.get(v, v) for v in (pref.shareable if pref else []) if v in SHARE_LABELS]
    if labels:
        parts.append("Kinds of things you may share, but only if they appear above: " + "; ".join(labels) + ".")
    away = _away_line(session, owner)
    if away:
        parts.append(away)
    if len(parts) == 1:
        parts.append("(None. You can only take a message.)")
    return [SystemBlock(persona, cache=True), SystemBlock("\n\n".join(parts))]


def _away_line(session: Session, owner: Person) -> str:
    """The one availability sentence allowed: away until a date, when the owner lets everyone know."""
    from superaide.triage.models import TriageSettings

    ts = session.scalar(select(TriageSettings).where(TriageSettings.person_id == owner.id))
    if ts is None or ts.away_until is None or ts.away_until <= datetime.now(UTC) or ts.away_visibility != "everyone":
        return ""
    return f"They are away until {ts.away_until:%A %d %B}. You may say that, and nothing more about it."


def run(
    llm: Any, *, model: str, system: list[SystemBlock], messages: list[dict[str, Any]]
) -> tuple[ReceptionTurn | None, Decimal]:
    """One call. Raises BudgetExceeded / AgentFrozen for the caller to answer politely."""
    result = llm.complete(
        purpose=PURPOSE,
        model=model,
        system=system,
        messages=messages,
        max_tokens=MAX_TOKENS,
        output_model=ReceptionTurn,
        effort=EFFORT,
    )
    parsed = result.parsed if isinstance(result.parsed, ReceptionTurn) else None
    return parsed, Decimal(result.cost_usd or 0)
