"""Promotion on a confidence bound, not a point estimate (#117, ADR 0010 section 8).

A stage is earned when the *lower* end of a Wilson interval on interrupt precision clears that stage's
gate, so 9 useful out of 10 doesn't buy what 90 out of 100 does. The same bound says when a stage is no
longer deserved. This module only measures and advises: it never changes `agent_state["stage"]` (the
owner accepts a promotion; Ava never promotes herself), and the gates are plain numbers in code.

Precision is `useful ÷ (useful + not_urgent)` over the decisions that told, or in shadow would have told,
the owner now, counting only feedback the owner gave (the inferred `useful` is left out). The reliability
table compares what the policy's score said with how often the owner agreed, in bins, per category.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.platform.tenancy.context import scope_or_default
from superaide.triage.models import TriageDecision
from superaide.triage.types import STAGE_ORDER, Decision, Feedback, Stage

Z = 1.645  # a one-sided 95% bound
TELL = {Decision.INTERRUPT, Decision.DEFER}

# Per target stage: (interrupt precision the lower bound must clear, feedback events needed). ADR 0010 section 8.
GATES: dict[Stage, tuple[float, int]] = {
    Stage.CHILD: (0.6, 30),
    Stage.ADOLESCENT: (0.7, 60),
    Stage.ADULT: (0.8, 100),
}
BINS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def wilson_lower(successes: int, n: int, z: float = Z) -> float:
    """The lower end of the Wilson score interval for a proportion. 0 with no data."""
    if n <= 0:
        return 0.0
    p = successes / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, (centre - margin) / (1 + z2 / n))


@dataclass(frozen=True)
class Verdict:
    stage: Stage
    next_stage: Stage | None
    useful: int
    not_urgent: int
    n: int
    precision: float | None
    lower: float
    needed_precision: float | None
    needed_events: int | None
    ready_to_offer: bool  # the lower bound clears the next gate on enough feedback (the owner still decides)
    demote_advised: bool  # the lower bound is below the gate this stage was earned at


def next_stage(stage: Stage) -> Stage | None:
    i = STAGE_ORDER.index(stage)
    return STAGE_ORDER[i + 1] if i + 1 < len(STAGE_ORDER) else None


def judge(stage: Stage, useful: int, not_urgent: int, z: float = Z) -> Verdict:
    n = useful + not_urgent
    lower = wilson_lower(useful, n, z)
    nxt = next_stage(stage)
    gate = GATES.get(nxt) if nxt else None
    earned_at = GATES.get(stage)  # the gate this stage was promoted through (None at Infant)
    return Verdict(
        stage=stage,
        next_stage=nxt,
        useful=useful,
        not_urgent=not_urgent,
        n=n,
        precision=None if n == 0 else round(useful / n, 4),
        lower=round(lower, 4),
        needed_precision=gate[0] if gate else None,
        needed_events=gate[1] if gate else None,
        ready_to_offer=bool(gate and n >= gate[1] and lower >= gate[0]),
        # Hysteresis: demote only when there is enough feedback to say, and the bound is under the gate
        # this stage was earned at.
        demote_advised=bool(earned_at and n >= earned_at[1] and lower < earned_at[0]),
    )


def _score_bin(score: float) -> int:
    for i in range(len(BINS) - 1):
        if score < BINS[i + 1]:
            return i
    return len(BINS) - 2


def reliability(rows: list[tuple[float, bool, str]]) -> list[dict[str, Any]]:
    """[(stated score, owner agreed it was urgent, category)] → per category and bin: how many, what the
    policy said on average, how often the owner agreed. A reliable score has predicted ≈ observed."""
    cells: dict[tuple[str, int], list[tuple[float, bool]]] = defaultdict(list)
    for score, agreed, category in rows:
        cells[(category, _score_bin(score))].append((score, agreed))
    out = []
    for (category, b), items in sorted(cells.items()):
        out.append(
            {
                "category": category,
                "bin": f"{BINS[b]:.1f}-{BINS[b + 1]:.1f}",
                "n": len(items),
                "predicted": round(sum(s for s, _ in items) / len(items), 4),
                "observed": round(sum(1 for _, a in items if a) / len(items), 4),
            }
        )
    return out


def ece(rows: list[tuple[float, bool, str]]) -> float | None:
    """Expected calibration error: the bin gaps between stated score and observed rate, weighted by size."""
    if not rows:
        return None
    cells = reliability([(s, a, "all") for s, a, _ in rows])
    return round(sum(c["n"] * abs(c["predicted"] - c["observed"]) for c in cells) / len(rows), 4)


def _told(d: TriageDecision) -> bool:
    shadow = (d.features or {}).get("shadow_decision")
    return d.decision in TELL or shadow in TELL


def measure(session: Session, stage: Stage) -> dict[str, Any]:
    """Read-only: the Wilson verdict for the next gate, the reliability table and the ECE, from the
    owner's own feedback on the decisions that told (or in shadow would have told) them."""
    rows = session.scalars(
        select(TriageDecision).where(
            TriageDecision.agent_id == scope_or_default().agent_id,
            TriageDecision.feedback.in_([Feedback.USEFUL, Feedback.NOT_URGENT]),
            TriageDecision.feedback_via.is_not(None),
            TriageDecision.feedback_via != "inferred",
        )
    ).all()
    told = [d for d in rows if _told(d)]
    useful = sum(1 for d in told if d.feedback == Feedback.USEFUL)
    not_urgent = len(told) - useful
    cal = [
        (
            float((d.features or {})["score"]),
            d.feedback == Feedback.USEFUL,
            str((d.classifier or {}).get("category") or "other"),
        )
        for d in told
        if isinstance((d.features or {}).get("score"), (int, float))
    ]
    v = judge(stage, useful, not_urgent)
    return {
        "verdict": {k: (str(x) if isinstance(x, Stage) else x) for k, x in v.__dict__.items()},
        "reliability": reliability(cal),
        "ece": ece(cal),
        "note": "Advice only. Ava never changes her own stage: you decide.",
    }
