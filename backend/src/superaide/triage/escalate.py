"""The escalation engine (ADR 0010 section 5): an interrupt is told to the owner rung by rung
until they explicitly acknowledge it, then the ladder stops.

    deferred ──(next_at)──▶ notifying(step n) ──(timeout)──▶ notifying(n+1) … ──▶ expired
                               ├──(ack / reply / call)──▶ acked ──(done)──▶ resolved
                               ├──(snooze 30m / 2h)─────▶ deferred (a new cycle)
                               └──(not urgent)──────────▶ resolved(dismissed) + feedback

Every step is a job (`escalation_step`) whose dedupe key is `escalation:{id}:step:{n}` (with
`:c{cycle}` after a snooze). The handler locks the escalation row and does nothing unless the
row is still at that step, so running a job twice, or a stale timer after an ack, changes
nothing. A delivery row is written before each send; on a retry, rungs already `sent` are
skipped and anything else is sent again: a duplicate push is better than a silent miss.

"Seen" is never an ack: only a button, an action in the app, or the owner's own reply count.
Buttons on Telegram and Slack (TR4) will call `ack` with the press's own id; today the app does.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from superaide.jobs.queue import cancel, enqueue, handler
from superaide.knowledge.profile import in_quiet_hours
from superaide.knowledge.store import preferences_for
from superaide.platform.audit import audit
from superaide.platform.db.models import Job, Person
from superaide.triage.features import next_local
from superaide.triage.models import Escalation, EscalationAck, EscalationDelivery, TriageDecision
from superaide.triage.store import settings_for
from superaide.triage.types import OPEN_STATES, Decision, EscalationState, Feedback, Resolution

log = logging.getLogger(__name__)

JOB_KIND = "escalation_step"
SNOOZE = {"snooze_30m": timedelta(minutes=30), "snooze_2h": timedelta(hours=2)}
ACKING = {"ack", "reply", "call"}
RESEND_PENDING_AFTER = timedelta(seconds=60)
QUOTE_HIDDEN = {"payment", "legal_hr"}  # never quoted, even in the app's push preview


def step_key(esc_id: uuid.UUID, step: int, cycle: int = 0) -> str:
    return f"escalation:{esc_id}:step:{step}" + (f":c{cycle}" if cycle else "")


def _now() -> datetime:
    return datetime.now(UTC)


# --- opening ---------------------------------------------------------------------------------


def open_escalation(
    session: Session,
    decision: TriageDecision,
    owner: Person,
    *,
    max_step: int,
    breaks_quiet: bool,
    defer_until: datetime | None,
    deadline_at: datetime | None,
    now: datetime | None = None,
) -> Escalation:
    """Create the escalation for an interrupt (or a deferral) and queue its first step."""
    now = now or _now()
    existing = session.scalar(select(Escalation).where(Escalation.decision_id == decision.id))
    if existing is not None:
        return existing
    deferred = defer_until is not None and defer_until > now
    esc = Escalation(
        decision_id=decision.id, owner_person_id=owner.id,
        state=EscalationState.DEFERRED if deferred else EscalationState.NOTIFYING,
        step=0, max_step=max_step, breaks_quiet=breaks_quiet, next_at=defer_until if deferred else now,
        deadline_at=deadline_at, cycle=0,
    )  # fmt: skip
    session.add(esc)
    session.flush()
    _queue(session, esc)
    audit(session, "triage", "escalation_opened", escalation_id=esc.id, decision_id=decision.id, state=esc.state)
    return esc


def _queue(session: Session, esc: Escalation) -> None:
    payload: dict[str, Any] = {"escalation_id": str(esc.id), "step": esc.step, "cycle": esc.cycle}
    if esc.state == EscalationState.DEFERRED and esc.next_at is not None:
        payload["at"] = esc.next_at.isoformat()
    enqueue(session, JOB_KIND, payload, run_at=esc.next_at, dedupe_key=step_key(esc.id, esc.step, esc.cycle))


def _same_moment(text: Any, moment: datetime) -> bool:
    """Whether the timer's `at` names the escalation's next_at, however the database wrote its offset."""
    try:
        return datetime.fromisoformat(str(text)) == moment
    except ValueError:
        return False


# --- one step --------------------------------------------------------------------------------


def _rung_providers(session: Session, owner: Person, rung: dict[str, Any]) -> list[str]:
    from superaide.proactive.notify import linked_channels

    external = [p for p in linked_channels(session, owner) if p != "app"]
    chosen = preferences_for(session, owner).interrupt_channel
    first = chosen if chosen in external else (external[0] if external else None)
    kind = rung.get("provider", "app")
    if kind == "app":
        return ["app"]
    if kind == "interrupt":
        return [first] if first else []
    if kind == "other":
        return [p for p in external if p != first][:1]
    return [kind] if kind in external else []


def _delay(session: Session, owner: Person, ladder: list[dict[str, Any]], step: int) -> timedelta:
    rung = ladder[step]
    if rung.get("provider") == "interrupt" and preferences_for(session, owner).interrupt_channel != "app":
        return timedelta(0)  # the owner chose a chat channel for interruptions: no wait for the app first
    return timedelta(seconds=int(rung.get("delay_s", 0)))


def text_for(session: Session, esc: Escalation, decision: TriageDecision, *, push: bool) -> str:
    sender = session.get(Person, decision.sender_person_id) if decision.sender_person_id else None
    # Never a name a stranger typed: this text is written into the owner's chat (issue #90).
    name = sender.display_name if sender else "Someone new"
    head = "Unverified request" if decision.decision == Decision.VERIFY else "Needs you"
    tries = f" ({esc.step + 1}{'nd' if esc.step == 1 else 'rd' if esc.step == 2 else 'th'} try)" if esc.step else ""
    line = f"{head}{tries}: {name}: {decision.reason or 'time-sensitive'}"
    if push:
        return line  # the lock-screen preview: the reason and the name, never a quote
    flags = set((decision.classifier or {}).get("risk_flags") or [])
    quotes = (decision.classifier or {}).get("evidence") or []
    if quotes and not flags & QUOTE_HIDDEN:
        line += "\n" + "\n".join(f"> {q}" for q in quotes[:3])
    return line


def _deliver(session: Session, esc: Escalation, decision: TriageDecision, owner: Person, provider: str) -> str:
    """Send one rung on one channel. Returns the delivery's status."""
    from superaide.channels import health
    from superaide.proactive import notify

    row = session.scalar(
        select(EscalationDelivery).where(
            EscalationDelivery.escalation_id == esc.id,
            EscalationDelivery.step == esc.step,
            EscalationDelivery.provider == provider,
            EscalationDelivery.cycle == esc.cycle,
        )
    )
    now = _now()
    if row is not None and (
        row.status == "sent" or (row.status == "pending" and now - row.created_at < RESEND_PENDING_AFTER)
    ):
        return row.status
    if row is None:
        row = EscalationDelivery(
            escalation_id=esc.id, step=esc.step, cycle=esc.cycle, provider=provider,
            token=secrets.token_urlsafe(9)[:12], status="pending",
        )  # fmt: skip
        session.add(row)
        session.flush()
    meta = {"kind": "escalation", "escalation_id": str(esc.id), "decision_id": str(decision.id), "step": esc.step}
    if provider == "app":
        notify.notify_owner(session, owner, text_for(session, esc, decision, push=False), meta, channel="app")
        row.status, row.sent_at = "sent", now
    elif health.rejected(session, provider):
        row.status, row.error = "skipped", "token rejected"
    else:
        identity = notify.identity_for(session, owner, provider)
        if identity is None:
            row.status, row.error = "skipped", "not linked"
        else:
            if provider == "telegram":
                sent, why = _push_with_buttons(identity, text_for(session, esc, decision, push=True), row)
            else:
                sent, why = notify._push(provider, identity, text_for(session, esc, decision, push=True))
            row.status, row.error = ("sent", "") if sent else ("failed", why[:500])
            if sent:
                row.sent_at = now
            else:
                from superaide.app.accounts import web_message
                from superaide.memory.store import get_or_create_context

                ctx = get_or_create_context(session, web_message(owner, ""))
                notify._note_failure(session, owner, ctx, provider, why, meta)
    audit(
        session, "triage", "escalation_step_sent" if row.status == "sent" else "escalation_step_failed",
        escalation_id=esc.id, step=esc.step, provider=provider, status=row.status,
    )  # fmt: skip
    return row.status


def _push_with_buttons(identity: Any, text: str, row: EscalationDelivery) -> tuple[bool, str]:
    """A Telegram push with Acknowledge / Snooze / Not urgent buttons (TR4). The message's
    "chat_id:message_id" is kept on the delivery so it can be edited once someone answers."""
    from superaide.channels import health
    from superaide.proactive import notify
    from superaide.triage.buttons import keyboard

    messenger = notify.messenger_for("telegram")
    if messenger is None or not hasattr(messenger, "send_buttons") or not notify.confirmed_here("telegram", identity):
        return notify._push("telegram", identity, text)  # explains why not, or sends plain text
    try:
        ref = messenger.send_buttons(identity.stable_key, text, keyboard(row.token))
    except health.DeliveryFailed as exc:
        return False, health.describe("telegram", {"status": exc.kind})
    if ref is None:
        return False, "Telegram didn't accept the message."
    row.external_ref = str(ref)[:200]
    return True, ""


def run_step(session: Session, payload: dict[str, Any], now: datetime | None = None) -> None:
    now = now or _now()
    esc = session.scalar(
        select(Escalation).where(Escalation.id == uuid.UUID(payload["escalation_id"])).with_for_update()
    )
    if esc is None:
        return
    step, cycle = int(payload.get("step", 0)), int(payload.get("cycle", 0))
    if esc.step != step or esc.cycle != cycle:
        return  # a stale timer: the ladder moved on (or was acked)
    if esc.state == EscalationState.DEFERRED:
        if esc.next_at is None or not _same_moment(payload.get("at"), esc.next_at):
            return
        esc.state = EscalationState.NOTIFYING
    elif esc.state != EscalationState.NOTIFYING:
        return

    decision = session.get(TriageDecision, esc.decision_id)
    owner = session.get(Person, esc.owner_person_id)
    if decision is None or owner is None:
        return
    ts = settings_for(session, owner)
    ladder = list(ts.ladder or [])
    last = min(esc.max_step, len(ladder) - 1)
    if step > last:  # the expiry timer
        esc.state, esc.resolution, esc.resolved_at = EscalationState.EXPIRED, Resolution.EXPIRED, now
        audit(session, "triage", "escalation_expired", escalation_id=esc.id)
        return

    providers = _rung_providers(session, owner, ladder[step])
    external = [p for p in providers if p != "app"]
    pref = preferences_for(session, owner)
    if external and in_quiet_hours(pref, now) and not esc.breaks_quiet:
        # No chat push inside quiet hours: wait for their end (bounded by a deadline), same rung.
        until = next_local(now, pref.timezone, pref.quiet_end)
        esc.state, esc.next_at, esc.cycle = EscalationState.DEFERRED, until, esc.cycle + 1
        _queue(session, esc)
        audit(session, "triage", "escalation_deferred", escalation_id=esc.id, reason="quiet", step=step)
        return

    statuses = [_deliver(session, esc, decision, owner, p) for p in providers]
    reached = "sent" in statuses
    if step < last:
        esc.step = step + 1
        # A rung that couldn't go out (not linked, token rejected, refused) isn't waited on.
        esc.next_at = now + (_delay(session, owner, ladder, esc.step) if reached else timedelta(0))
    else:
        esc.step = step + 1
        esc.next_at = now + timedelta(seconds=ts.expire_after_s)
    _queue(session, esc)


@handler(JOB_KIND)
def _step_job(session: Session, job: Job) -> None:
    run_step(session, dict(job.payload or {}))


# --- the owner's answer ------------------------------------------------------------------------


class AckRefused(PermissionError):
    pass


def ack(
    session: Session,
    esc_id: uuid.UUID,
    *,
    source: str,
    external_id: str,
    verb: str,
    actor: Person,
    now: datetime | None = None,
) -> bool:
    """Apply an explicit acknowledgement. True if it changed something; False for a duplicate press
    or an escalation that's already closed. Only the instance's owner may ack (anyone else is refused
    and audited)."""
    now = now or _now()
    esc = session.scalar(select(Escalation).where(Escalation.id == esc_id).with_for_update())
    if esc is None:
        return False
    if actor.id != esc.owner_person_id:
        audit(session, "triage", "escalation_ack_refused", escalation_id=esc.id, source=source, verb=verb)
        raise AckRefused("only the owner can acknowledge this")
    closable = esc.state in OPEN_STATES or (esc.state == EscalationState.ACKED and verb in {"done", "not_urgent"})
    if not closable:
        return False
    try:
        with session.begin_nested():
            session.add(EscalationAck(escalation_id=esc.id, source=source, external_id=external_id[:120], verb=verb,
                                      actor_person_id=actor.id, at=now))  # fmt: skip
            session.flush()
    except IntegrityError:
        return False  # the same press twice
    cancel(session, step_key(esc.id, esc.step, esc.cycle))
    decision = session.get(TriageDecision, esc.decision_id)
    if verb in SNOOZE:
        esc.state, esc.cycle, esc.step = EscalationState.DEFERRED, esc.cycle + 1, 0
        esc.next_at = now + SNOOZE[verb]
        _queue(session, esc)
    elif verb == "not_urgent":
        esc.state, esc.resolution, esc.resolved_at = EscalationState.RESOLVED, Resolution.DISMISSED, now
        if decision is not None:
            record_feedback(session, decision, Feedback.NOT_URGENT, via=source, now=now)
    elif verb == "done":
        esc.state, esc.resolution, esc.resolved_at = EscalationState.RESOLVED, Resolution.DONE, now
        esc.acked_at = esc.acked_at or now
        esc.acked_via = esc.acked_via or source
    else:  # ack | reply | call
        esc.state, esc.acked_at, esc.acked_via = EscalationState.ACKED, now, source
    audit(session, "triage", "escalation_acked", escalation_id=esc.id, source=source, verb=verb, state=esc.state)
    from superaide.triage.buttons import refresh

    via = {"app": "the app", "telegram": "Telegram", "slack": "Slack"}.get(source, source)
    refresh(session, esc, verb=verb, via=via)  # stale buttons go, on every copy
    return True


def record_feedback(
    session: Session, decision: TriageDecision, feedback: str, *, via: str, now: datetime | None = None
) -> None:
    decision.feedback, decision.feedback_at, decision.feedback_via = str(feedback), now or _now(), via
    audit(session, "triage", "triage_feedback", decision_id=decision.id, feedback=str(feedback), via=via)


def clear_feedback(session: Session, decision: TriageDecision) -> bool:
    """The owner takes a correction back (#117): the label is erased, and with it its influence on the
    kNN head. Returns whether there was one."""
    if decision.feedback is None:
        return False
    from superaide.triage import knn

    decision.feedback, decision.feedback_at, decision.feedback_via = None, None, None
    knn.forget(session, decision.id)
    audit(session, "triage", "triage_feedback_cleared", decision_id=decision.id)
    return True
