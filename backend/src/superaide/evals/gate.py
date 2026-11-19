"""The offline regression gates: long-term memory (#51) and human-likeness (#484).

Runs every scenario of a suite offline (fake model, free) and compares with its baseline file (`evals/ltm-baseline.json`
for the `ltm_*` categories, `evals/human-baseline.json` for the `human` category),
which records, per scenario, `pass` (must keep passing) or `known_gap` (an accepted failure, tracked).
It fails when a `pass` scenario fails, or when a scenario is missing from the baseline (so a new one
is recorded on purpose). A known gap that starts to pass is reported, and `--update` records it.

    uv run python -m superaide.evals gate [--suite ltm|human] [--update]
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from superaide.evals.scenario import CATEGORIES, Scenario, load_all

BASELINE = Path(__file__).resolve().parents[3] / "evals" / "ltm-baseline.json"
HUMAN_BASELINE = Path(__file__).resolve().parents[3] / "evals" / "human-baseline.json"
LTM = tuple(c for c in CATEGORIES if c.startswith("ltm_"))
HUMAN = ("human",)
SUITES = {"ltm": (LTM, BASELINE), "human": (HUMAN, HUMAN_BASELINE)}
# The lowest share of a category's scenarios that may pass, known gaps counted as failures. The per-scenario
# baseline stops a regression; this stops the category from being "fixed" by recording ever more known gaps.
MIN_PASS_RATE = {"ltm_time": 0.65, "human": 0.8}  # human: 20 of 24 pass today, the rest are tracked gaps


def ltm_scenarios() -> list[Scenario]:
    return [s for s in load_all() if s.category in LTM]


def human_scenarios() -> list[Scenario]:
    return [s for s in load_all() if s.category in HUMAN]


def load_baseline(path: Path = BASELINE) -> dict[str, str]:
    return json.loads(path.read_text()) if path.exists() else {}


def compare(results: dict[str, bool], baseline: dict[str, str]) -> tuple[list[str], list[str]]:
    """(regressions, improvements) for `results` (scenario id -> passed) against the baseline."""
    regressions, improvements = [], []
    for sid, passed in sorted(results.items()):
        want = baseline.get(sid)
        if want is None:
            regressions.append(f"{sid}: not in the baseline (run the gate with --update to record it)")
        elif want == "pass" and not passed:
            regressions.append(f"{sid}: passed in the baseline, fails now")
        elif want == "known_gap" and passed:
            improvements.append(f"{sid}: a known gap that passes now (run with --update to record it)")
    regressions += [f"{sid}: in the baseline but no longer a scenario" for sid in sorted(set(baseline) - set(results))]
    return regressions, improvements


def below_threshold(results: Mapping[str, bool], by_category: Mapping[str, str]) -> list[str]:
    """A line for each category whose pass rate (known gaps count as failures) is under its minimum."""
    out = []
    for category, minimum in MIN_PASS_RATE.items():
        mine = [ok for sid, ok in results.items() if by_category.get(sid) == category]
        if mine and sum(mine) / len(mine) < minimum:
            out.append(f"{category}: {sum(mine)}/{len(mine)} pass, below the {minimum:.0%} minimum")
    return out


def run_gate(update: bool = False, suite: str = "ltm") -> int:
    from superaide.evals.harness import reset_db, run_scenario

    categories, baseline_path = SUITES[suite]
    scenarios = [s for s in load_all() if s.category in categories]
    by_id = {sc.id: sc for sc in scenarios}
    reset_db()
    results: dict[str, bool] = {}
    errors: list[str] = []
    for sc in scenarios:
        run = run_scenario(sc)
        if run.error:
            errors.append(f"{sc.id}: harness error: {run.error.strip().splitlines()[-1]}")
        if run.graded:  # a scenario that is only graded live (no offline check) cannot gate CI
            results[sc.id] = run.passed
    if errors:
        print("\n".join(errors))
        return 2
    if update:
        new = {sid: "pass" if ok else "known_gap" for sid, ok in sorted(results.items())}
        baseline_path.write_text(json.dumps(new, indent=2) + "\n")
        print(f"baseline written: {sum(v == 'pass' for v in new.values())} pass, "
              f"{sum(v == 'known_gap' for v in new.values())} known gaps")  # fmt: skip
        return 0
    regressions, improvements = compare(results, load_baseline(baseline_path))
    for c in categories:
        mine = [sid for sid, sc in by_id.items() if sc.category == c and sid in results]
        floor = f" (minimum {MIN_PASS_RATE[c]:.0%})" if c in MIN_PASS_RATE else ""
        print(f"{c:18} {sum(results[i] for i in mine)}/{len(mine)} pass{floor}")
    by_category = {sid: sc.category for sid, sc in by_id.items()}
    low = below_threshold(results, by_category)
    for line in improvements:
        print("improved:", line)
    for line in low:
        print("BELOW THRESHOLD:", line)
    for line in regressions:
        print("REGRESSION:", line)
    return 1 if regressions or low else 0
