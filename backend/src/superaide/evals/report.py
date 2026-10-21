"""The scorecard: pass rate per category, pass^k, the failures with a transcript excerpt, and cost."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from superaide.evals.harness import Run
from superaide.evals.scenario import CATEGORIES, SEVERITY_RANK, Scenario

RESULTS_DIR = Path(__file__).resolve().parents[3] / "evals" / "results"


@dataclass
class Summary:
    scenario: Scenario
    runs: list[Run]

    @property
    def k(self) -> int:
        return len(self.runs)

    @property
    def passes(self) -> int:
        return sum(r.passed for r in self.runs)

    @property
    def all_pass(self) -> bool:
        return self.passes == self.k

    @property
    def graded(self) -> bool:
        """At least one check was actually graded (offline skips the model-dependent ones)."""
        return any(r.graded or r.error for r in self.runs)


def _pct(n: float) -> str:
    return f"{100 * n:.0f}%"


def build(summaries: list[Summary], *, live: bool, model: str, spent: Decimal, projection: str = "") -> dict[str, Any]:
    cats: dict[str, dict[str, Any]] = {}
    for cat in CATEGORIES:
        mine = [s for s in summaries if s.scenario.category == cat and s.graded]
        if not mine:
            continue
        expected_fail = [s for s in mine if s.scenario.known_gap]
        scored = [s for s in mine if not s.scenario.known_gap]
        runs = sum(s.k for s in scored)
        cats[cat] = {
            "scenarios": len(scored),
            "known_gaps": len(expected_fail),
            "mean": (sum(s.passes for s in scored) / runs) if runs else None,
            "pass_k": (sum(s.all_pass for s in scored) / len(scored)) if scored else None,
        }
    scored = [s for s in summaries if s.graded and not s.scenario.known_gap]
    runs = sum(s.k for s in scored)
    failures = []
    for s in summaries:
        for i, r in enumerate(s.runs):
            if r.passed:
                continue
            failures.append(
                {
                    "id": s.scenario.id,
                    "category": s.scenario.category,
                    "severity": s.scenario.severity,
                    "known_gap": s.scenario.known_gap,
                    "based_on": s.scenario.based_on,
                    "run": i + 1,
                    "fails": [f"{o.where}: {o.name}: {o.reason}" for o in r.outcomes if o.status == "fail"],
                    "error": r.error.strip().splitlines()[-1] if r.error else "",
                    "transcript": r.transcript(),
                }
            )
    failures.sort(key=lambda f: (bool(f["known_gap"]), SEVERITY_RANK[str(f["severity"])], f["id"], f["run"]))
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "live" if live else "offline",
        "model": model,
        "k": max((s.k for s in summaries), default=0),
        "cost_usd": float(spent),
        "projection": projection,
        "overall": {
            "scenarios": len(scored),
            "mean": (sum(s.passes for s in scored) / runs) if runs else None,
            "pass_k": (sum(s.all_pass for s in scored) / len(scored)) if scored else None,
        },
        "categories": cats,
        "scenarios": [
            {
                "id": s.scenario.id,
                "category": s.scenario.category,
                "severity": s.scenario.severity,
                "known_gap": s.scenario.known_gap,
                "passes": s.passes,
                "k": s.k,
                "cost_usd": float(sum((r.cost for r in s.runs), Decimal(0))),
                "skipped_checks": sum(o.status == "skip" for r in s.runs for o in r.outcomes),
            }
            for s in summaries
        ],
        "failures": failures,
    }


def markdown(data: dict[str, Any]) -> str:
    def rate(v: float | None) -> str:
        return "n/a" if v is None else _pct(v)

    o = data["overall"]
    lines = [
        f"# Ava reliability scorecard ({data['mode']})",
        "",
        f"Generated {data['generated_at']} · model `{data['model']}` · k = {data['k']} run(s) per scenario · "
        f"cost US${data['cost_usd']:.4f}",
        "",
        f"**Overall: mean {rate(o['mean'])}, pass^{data['k']} {rate(o['pass_k'])}** over {o['scenarios']} scenarios "
        "(known gaps excluded). pass^k counts a scenario only if every one of its k runs passed.",
        "",
    ]
    if data.get("projection"):
        lines += [data["projection"], ""]
    lines += ["| Category | Scenarios | Mean pass | pass^k | Known gaps |", "|---|---:|---:|---:|---:|"]
    for cat, c in data["categories"].items():
        lines.append(f"| {cat} | {c['scenarios']} | {rate(c['mean'])} | {rate(c['pass_k'])} | {c['known_gaps']} |")
    lines += ["", "## Scenarios", "", "| Scenario | Category | Severity | Passed | Cost |", "|---|---|---|---:|---:|"]
    for s in data["scenarios"]:
        tag = " (known gap)" if s["known_gap"] else ""
        lines.append(
            f"| {s['id']}{tag} | {s['category']} | {s['severity']} | {s['passes']}/{s['k']} | ${s['cost_usd']:.4f} |"
        )
    lines += ["", "## Failures", ""]
    if not data["failures"]:
        lines.append("None.")
    seen: set[str] = set()
    for f in data["failures"]:
        head = f"### {f['id']} ({f['severity']}, {f['category']}) run {f['run']}"
        if f["known_gap"]:
            head += f" — known gap: {f['known_gap']}"
        lines += [head, ""]
        if f["based_on"]:
            lines += [f"Guards against: {f['based_on']}", ""]
        for why in f["fails"]:
            lines.append(f"- {why}")
        if f["error"]:
            lines.append(f"- harness error: {f['error']}")
        if f["id"] not in seen:  # one transcript per scenario keeps the file readable
            seen.add(f["id"])
            lines += ["", "```", f["transcript"][:2500], "```"]
        lines.append("")
    return "\n".join(lines) + "\n"


def write(data: dict[str, Any], name: str, directory: Path = RESULTS_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    md, js = directory / f"{name}.md", directory / f"{name}.json"
    md.write_text(markdown(data))
    js.write_text(json.dumps(data, indent=2, default=str) + "\n")
    return md, js


def group(scenarios: list[Scenario], runs: dict[str, list[Run]]) -> list[Summary]:
    out: dict[str, Summary] = {}
    for sc in scenarios:
        out[sc.id] = Summary(sc, runs.get(sc.id, []))
    return [s for s in out.values() if s.runs]
