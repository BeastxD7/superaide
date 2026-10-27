"""`python -m superaide.evals triage [--live]`: how well triage decides, on the labelled set
(backend/evals/triage_labels.jsonl, ADR 0010 section 12).

Each label is a synthetic inbound message: who sent it (a persona), the channel, the owner's
local time and the text, with the expected decision and any others that are acceptable. The
report runs the real features → rules → classifier → policy path on each one, without a
database:

- offline (the default): rules only, no model, free; this is what an owner with no model gets;
- `--live`: the triage model on the `triage` role, under a hard spend cap;
- `--replay FILE`: the classifier answers saved in an earlier report, re-decided for free (to tune
  thresholds without paying for the model again).

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

from superaide.intentions.when import find_moment, zone
from superaide.memory.knowledge.profile import in_quiet_hours
from superaide.platform.db.models import OwnerPreference
from superaide.triage import classify, policy, rules
from superaide.triage.features import Features, next_local
from superaide.triage.types import Decision, Stage, TriageOutput

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
    decision: Decision  # at the label's stage
    hits: list[str]
    cost: Decimal
    shadow: Decision | None = None  # what Child would have done (at Infant the model can't interrupt alone)
    escalate: bool = False  # a verify escalated to the owner at the label's stage
    shadow_escalate: bool = False
    classified: dict[str, Any] | None = None  # the classifier's checked answer, kept for --replay

    @property
    def judged(self) -> Decision:
        """The decision the gates are measured on: the shadow decision at Infant (ADR 0010 section 8)."""
        return self.shadow if self.label.stage == Stage.INFANT and self.shadow is not None else self.decision

    @property
    def told(self) -> bool:
        """The owner was (or, at Infant, would have been) told now: an interrupt, a deferral, or an
        unverified request that would otherwise have interrupted."""
        infant = self.label.stage == Stage.INFANT and self.shadow is not None
        escalated = self.shadow_escalate if infant else self.escalate
        return self.judged in TELL or (self.judged == Decision.VERIFY and escalated)

    @property
    def ok(self) -> bool:
        if self.judged == Decision.VERIFY and self.told and self.label.expected in TELL:
            return True  # told now, and flagged as unverified: right for an urgent message with a risk
        return self.judged == self.label.expected or self.judged in self.label.also_ok


def decide(
    label: Label, *, llm: Any = None, model: str = "", ready: bool = False, replay: dict[str, Any] | None = None
) -> Result:
    """`replay`: a classifier answer saved by an earlier run (its `classified`), used instead of a call."""
    f = features_for(label)
    r = rules.evaluate(f)
    c: classify.Classified | None = None
    if r.decision is None:
        if replay is not None:
            c = classify.Classified(model="replay", fallback=str(replay.get("fallback", "")))
            if replay.get("output"):
                c.output = TriageOutput.model_validate(replay["output"])
                c.agreement = replay.get("agreement")
                c.deadline_at = datetime.fromisoformat(replay["deadline_at"]) if replay.get("deadline_at") else None
        else:
            c = classify.classify(llm, model=model, f=f, ready=ready)
    o = policy.decide(
        f, r, c.output if c else None, agreement=c.agreement if c else None, deadline_at=c.deadline_at if c else None
    )
    if c is not None and c.fallback:
        o.hits.append(f"classifier:{c.fallback}")
    kept = None
    if c is not None:
        kept = {
            "output": c.output.model_dump() if c.output else None, "agreement": c.agreement, "fallback": c.fallback,
            "deadline_at": c.deadline_at.isoformat() if c.deadline_at else None,
        }  # fmt: skip
    return Result(label, o.decision, o.hits, c.cost_usd if c else Decimal(0), o.shadow, o.escalate,
                  o.shadow_escalate, kept)  # fmt: skip


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def summarise(results: list[Result], *, mode: str) -> dict[str, Any]:
    # A label expected to be verify is judged by verify recall, not here: telling the owner about a
    # risky request now is neither a hit nor a false alarm.
    tp = sum(1 for x in results if x.told and x.label.expected in TELL)
    fp = sum(1 for x in results if x.told and x.label.expected not in TELL | {Decision.VERIFY})
    fn = sum(1 for x in results if not x.told and x.label.expected in TELL)
    v_hit = sum(1 for x in results if x.judged == Decision.VERIFY and x.label.expected == Decision.VERIFY)
    v_all = sum(1 for x in results if x.label.expected == Decision.VERIFY)
    confusion = Counter((x.label.expected.value, x.judged.value) for x in results)
    told_now = sum(1 for x in results if x.decision in TELL or x.escalate)  # what reached the owner at the stage
    return {
        "mode": mode,
        "labels": len(results),
        "synthetic": all(x.label.synthetic for x in results),
        "acceptable": _ratio(sum(x.ok for x in results), len(results)),
        "tell_now": {"precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn), "tp": tp, "fp": fp, "fn": fn},
        "verify_recall": _ratio(v_hit, v_all),
        "confusion": {f"{e}->{d}": n for (e, d), n in sorted(confusion.items())},
        "measured_on": "the shadow (Child) decision for Infant labels, else the decision",
        "interrupts_at_stage": told_now,
        "cost_usd": float(sum((x.cost for x in results), Decimal(0))),
        "misses": [
            {
                "id": x.label.id,
                "expected": x.label.expected.value,
                "got": x.judged.value,
                "hits": x.hits,
                "note": x.label.note,
            }
            for x in results
            if not x.ok
        ],
        "per_label": {x.label.id: x.classified for x in results if x.classified is not None},
    }


def markdown(s: dict[str, Any]) -> str:
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{100 * v:.0f}%"

    t = s["tell_now"]
    lines = [
        f"# Triage report ({s['mode']})",
        "",
        f"{s['labels']} labelled messages{' (synthetic)' if s['synthetic'] else ''}. Cost US${s['cost_usd']:.4f}.",
        f"Measured on {s['measured_on']} (ADR 0010 section 8). Interrupts that reached the owner at the "
        f"labels' stage: {s['interrupts_at_stage']}.",
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


def run(
    *,
    live: bool,
    key: str = "",
    cap: float = 0.25,
    out_dir: Path | None = None,
    name: str = "",
    replay: Path | None = None,
) -> dict[str, Any]:
    llm: Any = None
    model = ""
    if live:
        from superaide.brain.budget import BudgetGuard
        from superaide.brain.llm import ClaudeLLM
        from superaide.evals.harness import reset_db
        from superaide.platform.config import get_settings

        reset_db()  # the evals database: its schema, and its ledger for the cap
        llm, model = ClaudeLLM(BudgetGuard(cap), api_key=key), get_settings().model_triage
    saved: dict[str, Any] | None = json.loads(replay.read_text()).get("per_label", {}) if replay else None
    results = [
        decide(label, llm=llm, model=model, ready=live, replay=saved.get(label.id) if saved is not None else None)
        for label in load()
    ]
    mode = "live" if live else f"replay of {replay.name}" if replay else "offline, rules only"
    summary = summarise(results, mode=mode)
    out_dir = out_dir or LABELS.parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = name or f"triage-{'live' if live else 'offline'}"
    (out_dir / f"{stem}.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out_dir / f"{stem}.md").write_text(markdown(summary))
    return summary
