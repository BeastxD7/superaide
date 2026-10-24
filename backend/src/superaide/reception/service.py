"""The receptionist branch of the pipeline (ADR 0010 TR5): what someone Ava doesn't know gets in a DM.

Order, for a direct message on Telegram or Slack from an account Ava doesn't recognise:

1. A linking code ("123456") goes to the linking flow, unchanged: `None` here.
2. Receptionist off (the owner's switch), or not a DM: `None` (the old fixed reply).
3. "I'm <name>" still starts the linking challenge exactly as before. The model is never told whether
   that name matched anyone, and the same sentence about codes is added in code either way, so
   nobody can probe who Ava knows.
4. Over the per-sender limit (10 an hour, 30 a day by default): one polite fixed message per hour,
   then silence. No model call.
5. No model the owner pays for (no key, no credits), the receptionist's daily budget spent, or an
   unreadable answer: a fixed reply, and what they wrote is passed on as a message (code only).
6. Otherwise one receptionist turn (reception/turn.py).

A stranger's turn never learns, never touches memory, never proposes an action, and has no tools.
What they leave is stored as data (reception_message) and shown to the owner in the app; the
owner's chat and pushes only ever get fixed text, because the owner's own model reads that chat.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.channels.base import InboundMessage
from superaide.db.models import Context, Event, Person
from superaide.identity.resolver import Sender
from superaide.reception import turn as rturn
from superaide.reception.models import ReceptionMessage, ReceptionSettings

log = logging.getLogger(__name__)

PROVIDERS = ("telegram", "slack")
CLAIM_NOTE = (
    "If we already work together and you've linked another channel, I've sent a code there: type it here to "
    "link this account."
)
LIMIT_REPLY = "I've passed on what you sent. I can't keep chatting just now; please try again later."
FALLBACK = "I can't chat right now, but I've passed your message on. If you like, add your name and how to reach you."
PAUSED = "I'm paused right now, but I've passed your message on."
NOTE_EVERY = timedelta(minutes=15)
CHANNEL = {"telegram": "Telegram", "slack": "Slack"}


def settings_for(session: Session, owner: Person) -> ReceptionSettings:
    row = session.scalar(select(ReceptionSettings).where(ReceptionSettings.person_id == owner.id))
    if row is None:
        row = ReceptionSettings(person_id=owner.id)
        session.add(row)
        session.flush()
    return row


def greeting(agent_name: str, owner: Person, rs: ReceptionSettings) -> str:
    """The EU AI Act notice: who they're talking to. The owner's first name only if they allowed it."""
    name = rturn.first_name(owner) if rs.show_first_name else ""
    return f"Hi, I'm {agent_name}, {name}'s AI assistant." if name else f"Hi, I'm {agent_name}, an AI assistant."


def _counts(session: Session, sender: Sender, now: datetime) -> tuple[int, int]:
    def since(start: datetime) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.direction == "in",
                    Event.sender_identity_id == sender.identity.id,
                    Event.created_at >= start,
                )
            )
            or 0
        )

    return since(now - timedelta(hours=1)), since(now - timedelta(hours=24))


def _said_recently(session: Session, ctx: Context, text: str, now: datetime) -> bool:
    return (
        session.scalar(
            select(Event.id)
            .where(
                Event.context_id == ctx.id,
                Event.direction == "out",
                Event.content.contains(text),
                Event.created_at >= now - timedelta(hours=1),
            )
            .limit(1)
        )
        is not None
    )


def _first_reply(session: Session, ctx: Context) -> bool:
    return session.scalar(select(Event.id).where(Event.context_id == ctx.id, Event.direction == "out").limit(1)) is None


def handle_stranger(
    agent: Any, session: Session, sender: Sender, ctx: Context, msg: InboundMessage, inbound: Event
) -> str | None:
    """The reply for an unknown sender, "" for no reply, or None for the old fixed-reply path."""
    from superaide.app.accounts import owner as instance_owner
    from superaide.identity import linking

    if msg.provider not in PROVIDERS or ctx.kind != "dm":
        return None
    owner = instance_owner(session)
    if owner is None:
        return None
    rs = settings_for(session, owner)
    if not rs.enabled or linking.detect_code(msg.text) is not None:
        return None
    now = datetime.now(UTC)
    claimed = linking.detect_claimed_name(msg.text)
    if claimed:
        agent._claim(session, sender, claimed)  # the linking challenge, exactly as before
    hour, day = _counts(session, sender, now)
    if hour > rs.per_hour or day > rs.per_day:
        audit(session, "reception", "rate_limited", identity_id=sender.identity.id, hour=hour, day=day)
        return "" if _said_recently(session, ctx, LIMIT_REPLY, now) else LIMIT_REPLY

    first = _first_reply(session, ctx)
    hello = greeting(agent.name, owner, rs)
    reply, taken, mode = _talk(agent, session, owner, rs, ctx, msg)
    if mode != "model":  # couldn't talk: pass what they wrote on as a message
        taken = rturn.TakenMessage(message=msg.text[:2000])
    if taken is not None and (taken.message.strip() or taken.topic.strip()):
        store_message(
            agent, session, owner, sender, ctx, msg, inbound, taken, source="model" if mode == "model" else "code"
        )
    if first:  # the notice is code's job, not the model's: always, and only once per conversation
        reply = f"{hello} {reply}"
    if claimed:
        reply = f"{reply}\n\n{CLAIM_NOTE}"
    audit(session, "reception", "turn", identity_id=sender.identity.id, mode=mode, took_message=taken is not None)
    return reply


def _talk(
    agent: Any, session: Session, owner: Person, rs: ReceptionSettings, ctx: Context, msg: InboundMessage
) -> tuple[str, rturn.TakenMessage | None, str]:
    """(reply, taken message, mode): mode is model | code (no model, budget, bad answer) | paused."""
    from superaide.brain.budget import AgentFrozen, BudgetExceeded
    from superaide.brain.turn import build_messages
    from superaide.brain.wiring import brain_ready, with_credits
    from superaide.memory.store import recent_events

    if not brain_ready(agent.settings):
        return FALLBACK, None, "code"
    system = rturn.system_prompt(session, owner, rs, agent.name)
    messages = build_messages(recent_events(session, ctx, limit=rturn.HISTORY), msg.text)
    try:
        out, _cost = rturn.run(agent.llm, model=with_credits(agent.settings).model_triage, system=system,
                               messages=messages)  # fmt: skip
    except BudgetExceeded as exc:
        audit(session, "reception", "budget_cap_reached", period=exc.period)
        return FALLBACK, None, "code"
    except AgentFrozen:
        return PAUSED, None, "paused"
    except Exception:
        log.exception("receptionist turn failed")
        return FALLBACK, None, "code"
    if out is None or not out.reply.strip():
        return FALLBACK, None, "code"
    return out.reply.strip()[:1500], out.take_message, "model"


# --- the hand-off ----------------------------------------------------------------------------------


def store_message(
    agent: Any,
    session: Session,
    owner: Person,
    sender: Sender,
    ctx: Context,
    msg: InboundMessage,
    inbound: Event,
    taken: rturn.TakenMessage,
    *,
    source: str,
) -> ReceptionMessage:
    from superaide.asklink.service import rank_request

    now = datetime.now(UTC)
    deadline = _deadline(session, owner, taken.deadline_text, now)
    r = rank_request(session, owner, topic=taken.topic, why=taken.message, deadline=deadline, now=now)
    handle = f"@{msg.sender_username}" if msg.sender_username else msg.sender_display
    row = ReceptionMessage(
        identity_id=sender.identity.id, context_id=ctx.id, inbound_event_id=inbound.id, provider=msg.provider,
        handle=handle[:200], name=taken.name[:120], contact=taken.contact[:200], topic=taken.topic[:200],
        message=taken.message[:4000], deadline_text=taken.deadline_text[:120], claimed_urgency=taken.urgency,
        rank=r.rank, rule_hits=[h for h in r.hits if h != "source:ask_link"] + [f"source:{msg.provider}"],
        source=source,
    )  # fmt: skip
    session.add(row)
    session.flush()
    audit(session, "reception", "message_taken", message_id=row.id, rank=row.rank, source=source)
    if agent.settings.triage_enabled:
        _to_triage(session, owner, row, inbound, now)
    else:
        _note(session, owner, row, now)
    return row


def _deadline(session: Session, owner: Person, text: str, now: datetime) -> Any:
    from superaide.intentions.when import find_moment, zone
    from superaide.knowledge.store import preferences_for

    if not text.strip():
        return None
    found = find_moment(text, now.astimezone(zone(preferences_for(session, owner).timezone)))
    return found[0].date() if found and found[0] > now else None


def _reason(row: ReceptionMessage) -> str:
    where = CHANNEL.get(row.provider, row.provider)
    if row.rank == "unverified":
        return f"Message from someone new on {where} asks for something risky (money, codes or secrecy)"
    if row.rank == "urgent":
        return f"Message from someone new on {where} may be urgent"
    return f"Message taken from someone new on {where}"


def _to_triage(session: Session, owner: Person, row: ReceptionMessage, inbound: Event, now: datetime) -> None:
    """With triage on: a Needs-you item. Never `interrupt` (a stranger isn't verified): at most an
    unverified request that escalates, when it matches one of the owner's urgent topics."""
    from superaide.knowledge.profile import in_quiet_hours
    from superaide.knowledge.store import preferences_for
    from superaide.triage.escalate import open_escalation
    from superaide.triage.features import next_local
    from superaide.triage.models import TriageDecision
    from superaide.triage.store import current_stage
    from superaide.triage.types import POLICY_VERSION, Decision

    if session.scalar(select(TriageDecision.id).where(TriageDecision.inbound_event_id == inbound.id)) is not None:
        return
    topic_hit = any(h.startswith("R7_topic") for h in row.rule_hits)
    risky = row.rank == "unverified"
    decision = Decision.VERIFY if (risky or topic_hit) else Decision.HOLD
    # Strangers can ask for the ladder only through an urgent topic, only once a day each, and only
    # while the owner's interrupt budget has room: a message that repeats it can't ring the phone again.
    escalate = topic_hit and not risky and _may_escalate(session, owner, row, now)
    d = TriageDecision(
        inbound_event_id=inbound.id, sender_person_id=None, context_id=row.context_id,
        features={"relation": "stranger", "provider": row.provider, "reception_message_id": str(row.id),
                  "escalate": escalate, "maybe_urgent": row.rank == "urgent", "vip": False},
        rule_hits=["stranger", *row.rule_hits], decision=str(decision), reason=_reason(row),
        stage_at_time=str(current_stage(session)), policy_version=POLICY_VERSION,
    )  # fmt: skip
    session.add(d)
    session.flush()
    row.decision_id = d.id
    audit(session, "triage", "decided", decision_id=d.id, decision=d.decision, rule_hits=d.rule_hits, stage="-")
    if escalate:
        pref = preferences_for(session, owner)
        quiet_end = next_local(now, pref.timezone, pref.quiet_end) if in_quiet_hours(pref, now) else None
        open_escalation(session, d, owner, max_step=1, breaks_quiet=False, defer_until=quiet_end, deadline_at=None,
                        now=now)  # fmt: skip


def _may_escalate(session: Session, owner: Person, row: ReceptionMessage, now: datetime) -> bool:
    from superaide.triage.features import interrupts_since
    from superaide.triage.models import Escalation
    from superaide.triage.store import settings_for as triage_settings

    ts = triage_settings(session, owner)
    if interrupts_since(session, owner, now - timedelta(hours=1)) >= ts.max_per_hour:
        return False
    if interrupts_since(session, owner, now - timedelta(hours=24)) >= ts.max_per_day:
        return False
    earlier = session.scalar(
        select(func.count())
        .select_from(ReceptionMessage)
        .join(Escalation, Escalation.decision_id == ReceptionMessage.decision_id)
        .where(
            ReceptionMessage.identity_id == row.identity_id,
            ReceptionMessage.id != row.id,
            ReceptionMessage.created_at >= now - timedelta(hours=24),
        )
    )
    return not earlier


def _note(session: Session, owner: Person, row: ReceptionMessage, now: datetime) -> None:
    """With triage off: one short in-app note, fixed text only, at most every 15 minutes."""
    from superaide.proactive.notify import notify_owner

    recent = session.scalar(
        select(func.count())
        .select_from(ReceptionMessage)
        .where(ReceptionMessage.id != row.id, ReceptionMessage.created_at >= now - NOTE_EVERY)
    )
    if recent:
        return
    text = _reason(row) + ". Open your dashboard to read it."
    notify_owner(session, owner, text, {"kind": "message_taken", "message_id": str(row.id)}, channel="app")


def digest_lines(session: Session, owner: Person, since: datetime, now: datetime) -> list[str]:
    """Counts only (the digest may be pushed to a chat, and the owner's model reads it)."""
    rows = session.scalars(
        select(ReceptionMessage.rank).where(
            ReceptionMessage.agent_id == owner.agent_id,
            ReceptionMessage.created_at >= since,
            ReceptionMessage.created_at < now,
        )
    ).all()
    if not rows:
        return []
    urgent = sum(1 for r in rows if r in ("urgent", "unverified"))
    extra = f", {urgent} may be urgent or risky" if urgent else ""
    return [f"**Messages taken:** {len(rows)} from people I don't know{extra}. Read them in your dashboard."]
