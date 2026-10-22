"""Policy: turn features, rule hits, the classifier's checked output, the owner's thresholds
and the stage into one decision (ADR 0010 section 4). Pure functions, no database.

    score = w_u[urgency] × time_factor(deadline) × (agreement or confidence)
    interrupt if score ≥ θ_interrupt          (owner setting, start 0.6)
    handle    if the classifier can answer a granted intent, no risk flags, not needs_owner
    hold      otherwise

The stage caps what the *model* can cause on its own; a rule match (R5/R7) is the owner's own
instruction and interrupts at any stage. At Infant, a model-only interrupt becomes "hold, maybe
urgent", and the decision Child would have made is kept as the shadow decision, so the gates can
be measured before anything is promoted. Then R8 (quiet, away, busy) and R9 (interrupt budget)
turn an interrupt into a bounded deferral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from superaide.triage.features import Features, next_local
from superaide.triage.rules import RuleResult
from superaide.triage.types import URGENCY_RANK, Decision, Stage, TriageOutput

URGENCY_WEIGHT = {"low": 0.1, "normal": 0.3, "high": 0.7, "critical": 1.0}
CHILD_MARGIN = 0.1  # at Child the model alone must clear θ + 0.1
BORDERLINE = 0.1  # within ±0.1 of θ, resample (classify.py)
DEADLINE_LEAD = timedelta(minutes=30)
FULL_LADDER = 2  # rungs 0 (app), 1 (interrupt channel), 2 (the other channel); 3 (phone) is not built
MODEL_LADDER = {Stage.INFANT: 0, Stage.CHILD: 1, Stage.ADOLESCENT: FULL_LADDER, Stage.ADULT: FULL_LADDER}


@dataclass
class Outcome:
    decision: Decision
    hits: list[str] = field(default_factory=list)
    reason: str = ""
    shadow: Decision | None = None  # what the policy would have done at Child (shadow mode at Infant)
    score: float | None = None
    urgency: str | None = None
    maybe_urgent: bool = False
    from_rule: bool = False
    needs_approval: bool = False  # a handled reply is drafted for the owner to approve (Infant/Child)
    max_step: int = FULL_LADDER
    breaks_quiet: bool = False
    defer_until: datetime | None = None
    deadline_at: datetime | None = None
    app_note: bool = False  # a model-only interrupt the stage held back: an in-app note, no push (rung 0)
    # A verify that would otherwise have interrupted: escalated (as "unverified request"), never handled.
    escalate: bool = False
    shadow_escalate: bool = False


def time_factor(deadline: datetime | None, now: datetime) -> float:
    if deadline is None:
        return 0.85
    left = deadline - now
    if left <= timedelta(hours=2):
        return 1.0
    if left <= timedelta(hours=24):
        return 0.9
    return 0.6


def score(urgency: str, deadline: datetime | None, now: datetime, certainty: float) -> float:
    return round(URGENCY_WEIGHT[urgency] * time_factor(deadline, now) * max(0.0, min(1.0, certainty)), 4)


def borderline(value: float, theta: float) -> bool:
    return abs(value - theta) <= BORDERLINE


@dataclass
class _Base:
    decision: Decision
    hits: list[str]
    reason: str
    from_rule: bool
    score: float | None = None
    urgency: str | None = None
    maybe_urgent: bool = False
    vip_rule: bool = False
    handle_intent: str = ""
    otherwise: _Base | None = None  # for a verify: what the same answer without its risk flags decides


def _base(f: Features, r: RuleResult, out: TriageOutput | None, agreement: float | None, deadline) -> _Base:
    hits = list(r.hits)
    if r.decision is not None:
        why = {
            Decision.HOLD: "silenced by you" if any(h.startswith("R2") for h in hits) else "automated sender",
            Decision.VERIFY: "unverified request: " + ", ".join(r.risks),
            Decision.INTERRUPT: "VIP, and it looks urgent",
        }[r.decision]
        # A VIP rule may still need the classifier's urgency (R9: only a critical VIP gets past the budget).
        return _Base(r.decision, hits, why, True, urgency=out.urgency if out else None, vip_rule=r.vip_rule)
    if out is None:
        if r.topic_ids and f.known:
            return _Base(Decision.HOLD, hits, "matches an urgent topic; couldn't tell how urgent", True,
                         maybe_urgent=True)  # fmt: skip
        return _Base(Decision.HOLD, hits, "no rule matched; held for your digest", False)

    if out.risk_flags:
        hits += [f"R3_classifier:{flag}" for flag in out.risk_flags]
        # Never handled, but still interrupt-worthy if it otherwise would be (ADR 0010 section 2, R3).
        otherwise = _base(f, r, out.model_copy(update={"risk_flags": []}), agreement, deadline)
        return _Base(Decision.VERIFY, hits, "unverified request: " + ", ".join(out.risk_flags), True,
                     urgency=out.urgency, otherwise=otherwise)  # fmt: skip
    urgent = URGENCY_RANK[out.urgency] >= URGENCY_RANK["high"]
    if f.vip and (urgent or r.urgent_signal):
        hits.append("R5_vip")
        return _Base(Decision.INTERRUPT, hits, "VIP, and it looks urgent", True, urgency=out.urgency, vip_rule=True)
    known_ids = {f"t{i + 1}" for i in range(len(f.urgent_topics))}
    topics = list(dict.fromkeys([*r.topic_ids, *(t for t in out.matched_topics if t in known_ids)]))
    hits += [f"R7_topic:{t}" for t in topics if f"R7_topic:{t}" not in hits]
    if topics and f.known:
        # Only a verified sender's urgent topic interrupts by rule: an unverified account claiming an
        # outage could be anyone (a precision miss in the live labelled run).
        if urgent and f.verified:
            return _Base(Decision.INTERRUPT, hits, "urgent topic you asked to hear about", True, urgency=out.urgency)
        return _Base(Decision.HOLD, hits, "matches an urgent topic, but doesn't look urgent", True,
                     urgency=out.urgency, maybe_urgent=True)  # fmt: skip

    certainty = agreement if agreement is not None else out.confidence
    s = score(out.urgency, deadline, f.now, certainty)
    if s >= f.theta_interrupt:
        return _Base(Decision.INTERRUPT, hits, out.reason or "looks time-sensitive", False, s, out.urgency)
    can_handle = (
        out.can_answer != "no"
        and out.answer_intent
        and out.answer_intent in f.grants
        and not out.needs_owner
        and out.confidence >= f.theta_handle
    )
    if can_handle:
        return _Base(Decision.HANDLE, hits, f"answerable: {out.answer_intent}", False, s, out.urgency,
                     handle_intent=out.answer_intent)  # fmt: skip
    return _Base(Decision.HOLD, hits, out.reason or "not urgent", False, s, out.urgency)


def _cap(b: _Base, stage: Stage, f: Features) -> tuple[Decision, bool, int]:
    """(decision, maybe_urgent, max_step) once the stage has capped the model."""
    if b.decision != Decision.INTERRUPT or b.from_rule:
        return b.decision, b.maybe_urgent, FULL_LADDER
    if stage == Stage.INFANT:
        return Decision.HOLD, True, 0
    if stage == Stage.CHILD and (b.score or 0) < f.theta_interrupt + CHILD_MARGIN:
        return Decision.HOLD, True, 0
    return Decision.INTERRUPT, b.maybe_urgent, MODEL_LADDER[stage]


def _deferral(f: Features, b: _Base, deadline: datetime | None) -> tuple[datetime | None, list[str]]:
    """When an interrupt should wait (R8 quiet/away/busy, R9 budget), and until when. None: send now."""
    hits: list[str] = []
    critical_vip = f.vip and b.urgency == "critical"
    lead = deadline - DEADLINE_LEAD if deadline is not None else None
    waits: list[datetime] = []
    if f.quiet and not (b.vip_rule and f.vip_breaks_quiet) and f.quiet_end_at is not None:
        hits.append("R8_quiet")
        # Bounded by the deadline only for a critical VIP: anything else waits for the morning.
        waits.append(min(f.quiet_end_at, lead) if (lead is not None and critical_vip) else f.quiet_end_at)
    if f.away and not b.vip_rule and f.away_until is not None:
        hits.append("R8_away")
        waits.append(min(f.away_until, lead) if lead is not None else f.away_until)
    if f.busy and not f.vip and f.busy_until is not None:
        hits.append("R8_busy")
        waits.append(min(f.busy_until, lead) if lead is not None else f.busy_until)
    if f.budget_left <= 0:
        exempt = f.vip and (f.vip_ignores_budget == "always" or (f.vip_ignores_budget == "critical" and critical_vip))
        if not exempt:
            hits.append("R9_budget")
            hourly_out = f.interrupts_last_hour >= f.max_per_hour
            until = f.now + timedelta(hours=1) if hourly_out else next_local(f.now, f.timezone, "08:00")
            waits.append(until)
    if not waits:
        return None, hits
    until = max(waits)
    return (until if until > f.now else None), hits


def decide(
    f: Features,
    r: RuleResult,
    out: TriageOutput | None = None,
    *,
    agreement: float | None = None,
    deadline_at: datetime | None = None,
) -> Outcome:
    deadline = deadline_at or f.deadline_at
    b = _base(f, r, out, agreement, deadline)

    def finish(stage: Stage) -> Outcome:
        decision, maybe, max_step = _cap(b, stage, f)
        o = Outcome(
            decision, list(b.hits), b.reason, score=b.score, urgency=b.urgency, maybe_urgent=maybe,
            from_rule=b.from_rule, max_step=max_step, breaks_quiet=b.vip_rule and f.vip_breaks_quiet,
            deadline_at=deadline,
        )  # fmt: skip
        if decision == Decision.HANDLE:
            o.needs_approval = stage in (Stage.INFANT, Stage.CHILD)
        if decision == Decision.INTERRUPT:
            until, hits = _deferral(f, b, deadline)
            o.hits += hits
            if until is not None:
                o.decision, o.defer_until = Decision.DEFER, until
        if maybe and b.decision == Decision.INTERRUPT and decision == Decision.HOLD:
            o.reason, o.app_note = f"maybe urgent: {b.reason}", True
        if decision == Decision.VERIFY and b.otherwise is not None:
            # The same stage cap and breakpoints as the interrupt it would have been.
            would, _, steps = _cap(b.otherwise, stage, f)
            if would == Decision.INTERRUPT:
                until, hits = _deferral(f, b.otherwise, deadline)
                o.escalate, o.max_step, o.defer_until = True, steps, until
                o.hits += hits
                o.breaks_quiet = b.otherwise.vip_rule and f.vip_breaks_quiet
        return o

    outcome = finish(f.stage)
    child = finish(Stage.CHILD) if f.stage == Stage.INFANT else outcome
    outcome.shadow, outcome.shadow_escalate = child.decision, child.escalate
    return outcome
