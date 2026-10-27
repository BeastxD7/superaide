"""Owner Twin (issue #119): a small predictor of the owner's approve / reject choices, and how well it is calibrated.

The twin only reads the owner's decided approvals; it writes nothing and grants nothing. It is a count
model in code (no model call, no cost): for each kind of card, the chance of a yes is the owner's yes-rate
for that kind, shrunk toward their overall yes-rate while there are few examples. It is scored the honest
way, prequentially: every past decision is predicted from only the decisions before it, then revealed.

The calibration page reads those scores. `gate` says whether the twin has earned the right to be *trusted
as a signal* (enough decisions, accurate, calibrated, better than always guessing the common answer). It is
information for the owner; nothing in Superaide grants autonomy from it automatically. Edits are not
predicted because approvals cannot be edited yet. Off unless OWNER_TWIN_ENABLED.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.approvals.models import Approval

SHRINK = 2.0  # pseudo-decisions of the overall rate mixed into each kind
BINS = 5
MIN_DECISIONS = 30
MIN_ACCURACY = 0.90
MAX_CALIBRATION_ERROR = 0.10
MIN_LIFT = 0.02  # must beat always-guess-the-common-answer by this much


@dataclass
class Counts:
    yes: int = 0
    no: int = 0

    @property
    def n(self) -> int:
        return self.yes + self.no


@dataclass
class Twin:
    by_kind: dict[str, Counts] = field(default_factory=dict)
    total: Counts = field(default_factory=Counts)

    def p_yes(self, kind: str) -> float:
        overall = (self.total.yes + 1) / (self.total.n + 2)
        c = self.by_kind.get(kind, Counts())
        return (c.yes + SHRINK * overall) / (c.n + SHRINK)

    def learn(self, kind: str, approved: bool) -> None:
        for c in (self.by_kind.setdefault(kind, Counts()), self.total):
            c.yes += approved
            c.no += not approved


@dataclass(frozen=True)
class Calibration:
    decisions: int
    accuracy: float | None
    baseline: float | None
    brier: float | None
    calibration_error: float | None
    bins: list[dict[str, float | int]]
    per_kind: dict[str, dict[str, float | int]]
    gate: dict[str, object]


def decided(session: Session) -> list[Approval]:
    return list(
        session.scalars(
            select(Approval)
            .where(Approval.status.in_(("approved", "rejected")), Approval.decided_at.is_not(None))
            .order_by(Approval.decided_at, Approval.created_at)
        )
    )


def evaluate(rows: list[Approval]) -> Calibration:
    twin = Twin()
    scored: list[tuple[str, float, bool]] = []
    for a in rows:
        yes = a.status == "approved"
        scored.append((a.kind, twin.p_yes(a.kind), yes))  # predicted before the answer is known
        twin.learn(a.kind, yes)
    n = len(scored)
    if n == 0:
        return Calibration(0, None, None, None, None, [], {}, _gate(0, None, None, None))
    correct = sum((p >= 0.5) == yes for _, p, yes in scored)
    yes_total = sum(yes for *_, yes in scored)
    accuracy, baseline = correct / n, max(yes_total, n - yes_total) / n
    brier = sum((p - yes) ** 2 for _, p, yes in scored) / n
    bins = []
    err = 0.0
    for b in range(BINS):
        lo, hi = b / BINS, (b + 1) / BINS
        inside = [(p, y) for _, p, y in scored if lo <= p < hi or (b == BINS - 1 and p == 1.0)]
        if inside:
            said, saw = sum(p for p, _ in inside) / len(inside), sum(y for _, y in inside) / len(inside)
            err += len(inside) / n * abs(said - saw)
            bins.append(
                {"from": lo, "to": hi, "n": len(inside), "predicted": round(said, 3), "observed": round(saw, 3)}
            )
    per_kind: dict[str, dict[str, float | int]] = {}
    for kind in sorted({k for k, *_ in scored}):
        mine = [(p, y) for k, p, y in scored if k == kind]
        per_kind[kind] = {
            "decisions": len(mine),
            "yes_rate": round(sum(y for _, y in mine) / len(mine), 3),
            "accuracy": round(sum((p >= 0.5) == y for p, y in mine) / len(mine), 3),
        }
    return Calibration(
        n, round(accuracy, 3), round(baseline, 3), round(brier, 4), round(err, 3), bins, per_kind,
        _gate(n, accuracy, baseline, err),
    )  # fmt: skip


def _gate(n: int, accuracy: float | None, baseline: float | None, err: float | None) -> dict[str, object]:
    reasons = []
    if n < MIN_DECISIONS:
        reasons.append(f"needs {MIN_DECISIONS} decisions, has {n}")
    if accuracy is not None and accuracy < MIN_ACCURACY:
        reasons.append(f"accuracy {accuracy:.0%} is under {MIN_ACCURACY:.0%}")
    if err is not None and err > MAX_CALIBRATION_ERROR:
        reasons.append(f"calibration error {err:.2f} is over {MAX_CALIBRATION_ERROR}")
    if accuracy is not None and baseline is not None and accuracy < baseline + MIN_LIFT:
        reasons.append("no better than always guessing the common answer")
    return {
        "ready": n >= MIN_DECISIONS and not reasons,
        "reasons": reasons,
        "needs": {
            "decisions": MIN_DECISIONS, "accuracy": MIN_ACCURACY, "calibration_error": MAX_CALIBRATION_ERROR,
            "lift_over_baseline": MIN_LIFT,
        },
    }  # fmt: skip


def calibration(session: Session) -> Calibration:
    return evaluate(decided(session))
