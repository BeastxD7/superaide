"""`python -m superaide.evals triage [--live]`: how well triage decides, on the labelled set
(backend/evals/triage_labels.jsonl, ADR 0010 section 12).

Each label is a synthetic inbound message: who sent it (a persona), the channel, the owner's
local time and the text, with the expected decision and any others that are acceptable. The
report runs the real features → rules → classifier → policy path on each one, without a
database:

- offline (the default): rules only, no model, free; this is what an owner with no model gets;
- `--live`: the triage model on the `triage` role, under a hard spend cap.

It reports precision and recall for "tell the owner now" (interrupt or defer), recall for
"unverified request" (verify), how many decisions were acceptable, a confusion table and the cost.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from superaide.db.models import OwnerPreference
from superaide.intentions.when import find_moment, zone
from superaide.knowledge.profile import in_quiet_hours
from superaide.triage import classify, policy, rules
from superaide.triage.features import Features, next_local
from superaide.triage.types import Decision, Stage

LABELS = Path(__file__).resolve().parents[3] / "evals" / "triage_labels.jsonl"
TZ = "Asia/Kolkata"
DAY = datetime(2026, 10, 22)  # a Thursday; only the time of day matters
URGENT_TOPICS = ("server down", "payroll", "security breach")
TELL = {Decision.INTERRUPT, Decision.DEFER}
DECISIONS = [d.value for d in Decision]


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    synthetic: bool = True
    persona: Literal["colleague", "vip", "unverified_vip", "contact", "automated"]
    channel: Literal["slack", "telegram"] = "slack"
    local_time: str = "10:00"
    text: str
    expected: Decision
    also_ok: list[Decision] = Field(default_factory=list)
    stage: Stage = Stage.INFANT
    note: str = ""


def load(path: Path = LABELS) -> list[Label]:
    out = [Label.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
    if len({x.id for x in out}) != len(out):
        raise ValueError("duplicate label ids")
    return out


def features_for(label: Label) -> Features:
    local = datetime.combine(DAY.date(), time.fromisoformat(label.local_time), tzinfo=zone(TZ))
    now = local.astimezone(UTC)
    pref = OwnerPreference(quiet_start="22:00", quiet_end="07:00", timezone=TZ)
    quiet = in_quiet_hours(pref, now)
    found = find_moment(label.text, local)
    persona = label.persona
    return Features(
        text=label.text, now=now, timezone=TZ, sender_person_id=uuid.uuid4(),
        relation="colleague" if persona != "contact" else "contact",
        sender_kind="agent" if persona == "automated" else "human",
        sender_name={"automated": "Build bot", "vip": "Dana Lee", "unverified_vip": "Dana Lee"}.get(persona, "Priya"),
        vip=persona == "vip", trust=1 if persona == "unverified_vip" else 2, chat_kind="dm", provider=label.channel,
        quiet=quiet, quiet_end_at=next_local(now, TZ, "07:00") if quiet else None,
        stage=label.stage, urgent_topics=URGENT_TOPICS,
        deadline_at=found[0].astimezone(UTC) if found and found[0] > local else None,
    )  # fmt: skip


@dataclass
class Result:
    label: Label
    decision: Decision
    hits: list[str]
    cost: Decimal

    @property
    def ok(self) -> bool:
        return self.decision == self.label.expected or self.decision in self.label.also_ok


def decide(label: Label, *, llm: Any = None, model: str = "", ready: bool = False) -> Result:
    f = features_for(label)
    r = rules.evaluate(f)
    c = classify.classify(llm, model=model, f=f, ready=ready) if r.decision is None else None
    o = policy.decide(
        f, r, c.output if c else None, agreement=c.agreement if c else None, deadline_at=c.deadline_at if c else None
    )
    if c is not None and c.fallback:
        o.hits.append(f"classifier:{c.fallback}")
    return Result(label, o.decision, o.hits, c.cost_usd if c else Decimal(0))


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def summarise(results: list[Result], *, mode: str) -> dict[str, Any]:
    tp = sum(1 for x in results if x.decision in TELL and x.label.expected in TELL)
    fp = sum(1 for x in results if x.decision in TELL and x.label.expected not in TELL)
    fn = sum(1 for x in results if x.decision not in TELL and x.label.expected in TELL)
    v_hit = sum(1 for x in results if x.decision == Decision.VERIFY and x.label.expected == Decision.VERIFY)
    v_all = sum(1 for x in results if x.label.expected == Decision.VERIFY)
    confusion = Counter((x.label.expected.value, x.decision.value) for x in results)
    return {
        "mode": mode,
        "labels": len(results),
        "synthetic": all(x.label.synthetic for x in results),
        "acceptable": _ratio(sum(x.ok for x in results), len(results)),
        "tell_now": {"precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn), "tp": tp, "fp": fp, "fn": fn},
        "verify_recall": _ratio(v_hit, v_all),
        "confusion": {f"{e}->{d}": n for (e, d), n in sorted(confusion.items())},
        "cost_usd": float(sum((x.cost for x in results), Decimal(0))),
        "misses": [
            {
                "id": x.label.id,
                "expected": x.label.expected.value,
                "got": x.decision.value,
                "hits": x.hits,
                "note": x.label.note,
            }
            for x in results
            if not x.ok
        ],  # fmt: skip
    }


def markdown(s: dict[str, Any]) -> str:
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{100 * v:.0f}%"

    t = s["tell_now"]
    lines = [
        f"# Triage report ({s['mode']})",
        "",
        f"{s['labels']} labelled messages{' (synthetic)' if s['synthetic'] else ''}. Cost US${s['cost_usd']:.4f}.",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Acceptable decisions | {pct(s['acceptable'])} |",
        f"| Tell-now precision (interrupt or defer) | {pct(t['precision'])} ({t['tp']} right, {t['fp']} false) |",
        f"| Tell-now recall | {pct(t['recall'])} ({t['fn']} missed) |",
        f"| Verify recall (risky requests caught) | {pct(s['verify_recall'])} |",
        "",
        "| Expected | " + " | ".join(DECISIONS) + " |",
        "|---|" + "---:|" * len(DECISIONS),
    ]
    for e in DECISIONS:
        row = [str(s["confusion"].get(f"{e}->{d}", 0)) for d in DECISIONS]
        lines.append(f"| {e} | " + " | ".join(row) + " |")
    if s["misses"]:
        lines += ["", "## Not acceptable", ""]
        lines += [f"- {m['id']}: expected {m['expected']}, got {m['got']} ({m['note']})" for m in s["misses"]]
    return "\n".join(lines) + "\n"


def run(*, live: bool, key: str = "", cap: float = 0.25, out_dir: Path | None = None, name: str = "") -> dict[str, Any]:
    llm: Any = None
    model = ""
    if live:
        from superaide.brain.budget import BudgetGuard
        from superaide.brain.llm import ClaudeLLM
        from superaide.config import get_settings
        from superaide.evals.harness import reset_db

        reset_db()  # the evals database: its schema, and its ledger for the cap
        llm, model = ClaudeLLM(BudgetGuard(cap), api_key=key), get_settings().model_triage
    results = [decide(label, llm=llm, model=model, ready=live) for label in load()]
    summary = summarise(results, mode="live" if live else "offline, rules only")
    out_dir = out_dir or LABELS.parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = name or f"triage-{'live' if live else 'offline'}"
    (out_dir / f"{stem}.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out_dir / f"{stem}.md").write_text(markdown(summary))
    return summary
