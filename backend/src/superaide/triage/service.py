"""Triage one inbound message end to end: features → rules → classifier → policy → record →
escalate (ADR 0010 section 1).

Runs as a background job (`triage_inbound`, once per message) queued by the pipeline when
TRIAGE_ENABLED is on. It is shadow mode until the pipeline hook (TR5) lands: the decision is
recorded and the owner is told about interrupts, deferrals and unverified requests, but the
sender's reply is the one the pipeline already sent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.db.models import Event, Job, Person
from superaide.jobs.queue import enqueue, handler
from superaide.triage import classify, policy, rules
from superaide.triage.escalate import open_escalation
from superaide.triage.features import gather
from superaide.triage.models import TriageDecision
from superaide.triage.types import POLICY_VERSION, Decision

log = logging.getLogger(__name__)

JOB_KIND = "triage_inbound"
EARLIER = 2  # earlier messages from the same sender in the same thread, for the classifier


def schedule(session: Session, event: Event) -> None:
    """Queue triage of one inbound message (once per message)."""
    enqueue(session, JOB_KIND, {"event_id": str(event.id)}, dedupe_key=f"triage:{event.id}", max_attempts=3)


def _earlier(session: Session, event: Event) -> list[str]:
    rows = session.scalars(
        select(Event.content)
        .where(
            Event.context_id == event.context_id,
            Event.direction == "in",
            Event.resolved_person_id == event.resolved_person_id,
            Event.thread_id.is_(None) if event.thread_id is None else Event.thread_id == event.thread_id,
            Event.created_at < event.created_at,
        )
        .order_by(Event.created_at.desc())
        .limit(EARLIER)
    ).all()
    return list(reversed(rows))


def triage_event(
    session: Session, event_id: uuid.UUID, *, llm: Any, model: str, ready: bool, now: datetime | None = None
) -> TriageDecision | None:
    """Decide about one message. Idempotent: a message already decided returns its decision."""
    from superaide.app.accounts import owner as instance_owner

    existing = session.scalar(select(TriageDecision).where(TriageDecision.inbound_event_id == event_id))
    if existing is not None:
        return existing
    event = session.get(Event, event_id)
    owner = instance_owner(session)
    if event is None or owner is None or event.direction != "in" or event.resolved_person_id is None:
        return None
    if event.resolved_person_id == owner.id:
        return None  # the owner's own turns never go through triage
    sender = session.get(Person, event.resolved_person_id)
    if sender is None or sender.relation == "unknown":
        return None  # strangers keep the fixed reply (stranger_intake waits for TR5)

    now = now or datetime.now(UTC)
    f = gather(session, event=event, owner=owner, now=now)
    return decide(session, event, owner, sender, f, llm=llm, model=model, ready=ready, now=now)


def decide(
    session: Session, event: Event, owner: Person, sender: Person | None, f: Any, *, llm: Any, model: str,
    ready: bool, now: datetime | None = None,
) -> TriageDecision:  # fmt: skip
    """Rules → classifier → policy → record → tell the owner, for one inbound item whose features
    are gathered (a message here, an email in skills/gmail_inbox.py). `ready` False: rules only."""
    now = now or datetime.now(UTC)
    r = rules.evaluate(f)
    # The classifier runs when the rules didn't decide, or for a VIP over budget (only a critical one gets past).
    wants_model = r.decision is None or (r.vip_rule and f.budget_left <= 0)
    c = classify.classify(llm, model=model, f=f, earlier=_earlier(session, event), ready=ready) if wants_model else None
    out = c.output if c is not None else None
    o = policy.decide(f, r, out, agreement=c.agreement if c else None, deadline_at=c.deadline_at if c else None)
    if c is not None and c.fallback:
        o.hits.append(f"classifier:{c.fallback}")
        if c.fallback == "bad_quote":
            o.reason = "couldn't classify"
        if c.fallback == "budget":
            audit(session, "triage", "triage_budget_fallback", event_id=event.id)

    features = f.snapshot()
    features.update(
        shadow_decision=str(o.shadow or o.decision), maybe_urgent=o.maybe_urgent, score=o.score,
        needs_approval=o.needs_approval, from_rule=o.from_rule, urgency=o.urgency, escalate=o.escalate,
        defer_until=o.defer_until.isoformat() if o.defer_until else None,
    )  # fmt: skip
    row = TriageDecision(
        inbound_event_id=event.id, sender_person_id=sender.id if sender else None, context_id=event.context_id,
        features=features,
        rule_hits=o.hits, classifier=out.model_dump() if out else None, classifier_model=c.model if c else "",
        cost_usd=c.cost_usd if c else 0, agreement=c.agreement if c else None, decision=str(o.decision),
        reason=o.reason[:500], stage_at_time=str(f.stage), policy_version=POLICY_VERSION,
    )  # fmt: skip
    session.add(row)
    session.flush()
    audit(session, "triage", "decided", decision_id=row.id, decision=row.decision, rule_hits=o.hits,
          stage=str(f.stage))  # fmt: skip

    if f.quiet and f.quiet_end_at is not None:  # one note when quiet hours end, if anything came in
        from superaide.triage.digest import schedule_catchup

        schedule_catchup(session, owner, f.quiet_end_at)
    if o.decision in (Decision.INTERRUPT, Decision.DEFER) or o.escalate:
        open_escalation(
            session, row, owner, max_step=o.max_step, breaks_quiet=o.breaks_quiet,
            defer_until=o.defer_until if o.decision == Decision.DEFER or o.escalate else None,
            deadline_at=o.deadline_at, now=now,
        )  # fmt: skip
    elif o.decision == Decision.VERIFY or o.app_note:
        # Shown to the owner in the app (rung 0), never pushed: an unverified request, or a
        # model-only "maybe urgent" the stage held back.
        _app_note(session, owner, row)
    return row


def _app_note(session: Session, owner: Person, row: TriageDecision) -> None:
    from superaide.proactive.notify import notify_owner

    sender = session.get(Person, row.sender_person_id) if row.sender_person_id else None
    head = "Unverified request" if row.decision == Decision.VERIFY else "Maybe urgent"
    name = sender.display_name if sender else ""
    if not name:  # an email from someone who isn't a contact: the name on the email
        event = session.get(Event, row.inbound_event_id)
        name = str(((event.meta if event is not None else None) or {}).get("from_name") or "Someone")
    text = f"{head}: {name}: {row.reason}"
    notify_owner(session, owner, text, {"kind": "triage", "decision_id": str(row.id)}, channel="app")


@handler(JOB_KIND)
def _job(session: Session, job: Job) -> None:
    from superaide.factory import brain_ready, build_llm
    from superaide.vault.store import runtime_settings

    settings = runtime_settings()
    ready = brain_ready(settings)
    llm = build_llm(settings) if ready else None
    triage_event(session, uuid.UUID(job.payload["event_id"]), llm=llm, model=settings.model_triage, ready=ready)
