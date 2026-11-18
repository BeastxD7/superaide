"""The latency budget of a default direct-message turn (#413), checked in CI.

Wall-clock time is too noisy to fail a build on, so the budget is stated on a hand-driven clock: tests/observability/
test_latency_budget.py runs a turn with the fake model and charges ONE millisecond for every database statement the
turn runs (the part of a turn that grows when someone adds an N+1 query or a new lookup). A stage that costs more
than its line below fails CI. The model, the network and the channel send are fixed fakes, so the numbers are exact
and the same on every machine; real timings per stage and channel are in the console (#412, p50 and p95).

How the numbers were set: measured on main (identity 10, retrieval 5, reply 2, total 28 for 30 statements), then
about 50 to 60 percent headroom so ordinary work fits and a doubling does not. Raising a line is a decision for the
pull request that does it, with the reason in its description.

Not in the budget on purpose: heavy extras (a second opinion, a dissent pass) are off the default path, and the test
pins that a default turn makes exactly ONE model call.
"""

from __future__ import annotations

from dataclasses import dataclass

# Milliseconds on the statement-counting clock (1 ms per statement). A stage missing here has no line: it is 0.
STAGE_BUDGET_MS: dict[str, int] = {
    "verify": 2,
    "identity": 16,
    "gate": 8,
    "retrieval": 8,
    "airlock": 4,
    "model": 4,
    "policy": 6,
    "reply": 4,
    "send": 4,
}
TURN_BUDGET_MS = 45
MAX_MODEL_CALLS = 1  # a default DM turn: one reply call; extras run after the reply or not at all
# The order a turn must meet these stages in. Anything that reaches the model (retrieval, the prompt) comes after the
# audience gate; the tests pin it so no later change (a parallel stage) can read memory before the audience is known.
PRIVACY_ORDER = ("identity", "gate", "retrieval", "model")


@dataclass(frozen=True)
class Overrun:
    what: str
    spent: int
    budget: int

    def __str__(self) -> str:
        return f"{self.what}: spent {self.spent}, budget {self.budget}"


def overruns(stage_ms: dict[str, int], *, total_ms: int, model_calls: int) -> list[Overrun]:
    found = [
        Overrun(f"stage {n}", ms, STAGE_BUDGET_MS.get(n, 0))
        for n, ms in stage_ms.items()
        if ms > STAGE_BUDGET_MS.get(n, 0)
    ]
    if total_ms > TURN_BUDGET_MS:
        found.append(Overrun("the whole turn", total_ms, TURN_BUDGET_MS))
    if model_calls > MAX_MODEL_CALLS:
        found.append(Overrun("model calls", model_calls, MAX_MODEL_CALLS))
    return found


def order_violations(order: list[str]) -> list[str]:
    """Stages entered out of PRIVACY_ORDER (first appearances compared), or retrieval or the model with no gate."""
    first = {name: order.index(name) for name in PRIVACY_ORDER if name in order}
    seen = [n for n in PRIVACY_ORDER if n in first]
    bad = [f"{b} started before {a}" for a, b in zip(seen, seen[1:], strict=False) if first[b] < first[a]]
    if "retrieval" in first and "gate" not in first:
        bad.append("retrieval ran with no audience gate")
    if "model" in first and "gate" not in first:
        bad.append("the model ran with no audience gate")
    return bad
