"""Compare Ava's own memory search with an outside system on the same notes (#51).

Only the logic lives here, so it is testable offline with a stand-in system. The real comparison against
the open-source Mem0 is `backend/evals/baselines/mem0_baseline.py`: opt-in, its dependency is the optional
`eval-mem0` extra (never installed by default), and it is never run in CI or imported by the app.

What is compared: for each `ltm_*` scenario that checks the owner's recall (`recall_top` as the owner), the
notes of that scenario's world (including its filler) are loaded into both systems and each is asked the
same question. A case passes when every expected pattern is in the top k and no `absent` pattern is. The
audience tiers (colleague, group) are Ava's own gate: an outside store has no equivalent, so they are
not compared.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select

from superaide.evals.checks import _found, _list, _recalled
from superaide.evals.scenario import Scenario
from superaide.memory.models import MemoryItem
from superaide.platform.db.session import session_scope


class OutsideMemory(Protocol):
    """What a system under comparison must offer: start empty, take notes, answer a query."""

    name: str

    def reset(self) -> None: ...
    def add(self, notes: list[str]) -> None: ...
    def search(self, query: str, k: int) -> list[str]: ...


@dataclass(frozen=True)
class Case:
    scenario: str
    query: str
    contains: tuple[str, ...]
    absent: tuple[str, ...]
    k: int


@dataclass
class Row:
    case: Case
    ava: bool
    other: bool
    ava_top: list[str] = field(default_factory=list)
    other_top: list[str] = field(default_factory=list)


def read_only(sc: Scenario) -> bool:
    """True when the scenario only asks questions of a seeded world. A scenario that teaches Ava something in
    its turns (a correction, a shared file) is about her learning, which an outside store is not given."""
    return not any(t.fake or getattr(t, "share", None) or t.advance or t.press for t in sc.turns)


def owner_cases(sc: Scenario) -> list[Case]:
    """The owner's recall questions of a scenario (turn expectations and end checks)."""
    if not read_only(sc):
        return []
    items = [it for t in sc.turns for it in t.expect] + list(sc.checks)
    out = []
    for it in items:
        spec = it.get("recall_top")
        if spec and str(spec.get("as", "owner")) == "owner":
            out.append(
                Case(
                    sc.id,
                    str(spec["query"]),
                    tuple(_list(spec.get("contains") or [])),
                    tuple(_list(spec.get("absent") or [])),
                    int(spec.get("k", 5)),
                )  # fmt: skip
            )
    return out


def grade(case: Case, got: list[str]) -> bool:
    top = got[: case.k]
    return all(any(_found(p, g) for g in top) for p in case.contains) and not any(
        any(_found(p, g) for g in top) for p in case.absent
    )


def owner_notes() -> list[str]:
    """Every current note in the world just built (the owner may see them all): what both systems are given."""
    with session_scope() as s:
        return list(
            s.scalars(select(MemoryItem.content).where(MemoryItem.valid_to.is_(None)).order_by(MemoryItem.created_at))
        )


def compare(
    scenarios: list[Scenario], other: OutsideMemory, build: Callable[[Scenario], None] | None = None
) -> list[Row]:
    """Build each scenario's world, load its notes into `other`, ask both the same questions."""
    from superaide.evals.harness import build_world, reset_db, start_time

    rows: list[Row] = []
    for sc in scenarios:
        cases = owner_cases(sc)
        if not cases or not sc.world.memories:
            continue
        reset_db()
        (build or (lambda s: build_world(s, start_time(s))))(sc)
        other.reset()
        other.add(owner_notes())
        for c in cases:
            ava = _recalled({"query": c.query, "k": c.k, "as": "owner"})
            theirs = other.search(c.query, c.k)
            rows.append(Row(c, grade(c, ava), grade(c, theirs), ava[: c.k], theirs[: c.k]))
    return rows


def render(rows: list[Row], other_name: str) -> str:
    n = len(rows)
    ava, other = sum(r.ava for r in rows), sum(r.other for r in rows)
    lines = [
        f"# Memory recall: Ava vs {other_name}",
        "",
        f"{n} owner recall questions from the `ltm_*` scenarios, same seeded notes in both (scenarios that teach Ava "
        f"something in their turns are left out). Ava: **{ava}/{n}**. "
        f"{other_name}: **{other}/{n}**.",
        "",
        f"| Scenario | Question | Ava | {other_name} |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.case.scenario} | {r.case.query} | {'pass' if r.ava else 'miss'} | {'pass' if r.other else 'miss'} |"
        )
    lines += ["", "Audience tiers are not compared: the outside store has no audience gate.", ""]
    return "\n".join(lines)


def results_text(raw: Any) -> list[str]:
    """Texts out of an outside system's answer (a list, or {"results": [...]}) whose items are strings or
    dicts with a `memory` or `text` field."""
    items = raw.get("results", []) if isinstance(raw, dict) else raw
    out = []
    for it in items:
        out.append(str(it.get("memory") or it.get("text") or "") if isinstance(it, dict) else str(it))
    return out
