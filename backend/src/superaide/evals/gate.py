"""The long-term-memory regression gate (#51).

Runs every `ltm_*` scenario offline (fake model, free) and compares with `evals/ltm-baseline.json`,
which records, per scenario, `pass` (must keep passing) or `known_gap` (an accepted failure, tracked).
It fails when a `pass` scenario fails, or when a scenario is missing from the baseline (so a new one
is recorded on purpose). A known gap that starts to pass is reported, and `--update` records it.

    uv run python -m superaide.evals gate [--update]
"""

from __future__ import annotations

import json
from pathlib import Path

from superaide.evals.scenario import CATEGORIES, Scenario, load_all

BASELINE = Path(__file__).resolve().parents[3] / "evals" / "ltm-baseline.json"
LTM = tuple(c for c in CATEGORIES if c.startswith("ltm_"))


def ltm_scenarios() -> list[Scenario]:
    return [s for s in load_all() if s.category in LTM]


def load_baseline() -> dict[str, str]:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {}


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


def run_gate(update: bool = False) -> int:
    from superaide.evals.harness import reset_db, run_scenario

    reset_db()
    results: dict[str, bool] = {}
    errors: list[str] = []
    for sc in ltm_scenarios():
        run = run_scenario(sc)
        if run.error:
            errors.append(f"{sc.id}: harness error: {run.error.strip().splitlines()[-1]}")
        results[sc.id] = run.passed
    if errors:
        print("\n".join(errors))
        return 2
    if update:
        new = {sid: "pass" if ok else "known_gap" for sid, ok in sorted(results.items())}
        BASELINE.write_text(json.dumps(new, indent=2) + "\n")
        print(f"baseline written: {sum(v == 'pass' for v in new.values())} pass, "
              f"{sum(v == 'known_gap' for v in new.values())} known gaps")  # fmt: skip
        return 0
    regressions, improvements = compare(results, load_baseline())
    by_cat = {c: [s for s in ltm_scenarios() if s.category == c] for c in LTM}
    for c, scs in by_cat.items():
        ok = sum(results[s.id] for s in scs)
        print(f"{c:18} {ok}/{len(scs)} pass")
    for line in improvements:
        print("improved:", line)
    for line in regressions:
        print("REGRESSION:", line)
    return 1 if regressions else 0
