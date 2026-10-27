"""The receptionist branch of the pipeline (ADR 0010 TR5): what someone Ava doesn't know gets in a DM.

Order, for a direct message on Telegram or Slack from an account Ava doesn't recognise:

1. A linking code ("123456") goes to the linking flow, unchanged: `None` here.
2. Receptionist off (the owner's switch), or not a DM: `None` (the old fixed reply).
3. "I'm <name>" still starts the linking challenge exactly as before. The model is never told whether
   that name matched anyone, and the same sentence about codes is added in code either way, so
   nobody can probe who Ava knows.
4. Code stores every message before it decides whether to answer (#474): the model only adds structure
   (name, topic, deadline), and a message it did not structure is stored as it was typed. A reply says "passed
   on" only after the row was written; if storing failed, the reply says so instead.
5. Over the per-sender reply limit (10 an hour, 30 a day by default): no model call. One fixed notice says
   when Ava will answer again, then silence; what they send meanwhile is still stored. The first reply tells
   the hourly number, once a warning says how many replies are left, and the first reply after the reset says
   "I can reply again".
6. No model the owner pays for (no key, no credits), the receptionist's daily budget spent, or an
   unreadable answer: a fixed reply, and what they wrote is passed on as a message (code only).
7. Otherwise one receptionist turn (reception/turn.py).

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

from superaide.channels.base import InboundMessage
from superaide.identity.models import Person
from superaide.identity.resolver import Sender
from superaide.memory.models import Context, Event
from superaide.platform.audit import audit
from superaide.reception import turn as rturn
from superaide.reception.models import ReceptionMessage, ReceptionSettings

log = logging.getLogger(__name__)

PROVIDERS = ("telegram", "slack")
CLAIM_NOTE = (
    "If we already work together and you've linked another channel, I've sent a code there: type it here to "
    "link this account."
)
# Every sentence the sender is told about limits is a code template filled with the real numbers (#474); the
# marks are what `_said_recently` looks for, so a notice is never repeated within its window.
CAP_NOTICE = (
    "You can send up to {n} messages an hour here. After that I'll stop replying, but I'll still pass everything on."
)
NEAR_MARK = " more time"
NEAR_NOTICE = (
    "I can reply {left} more time{s} this hour. After that I'll stop replying, but I'll still pass everything on."
)
LIMIT_MARK = "reply limit for now"
LIMIT_REPLY = (
    "I've reached my reply limit for now, so I'll stop replying until {when}. "
    "What you send meanwhile is still passed on."
)
RESUMED = "I can reply again."
SAVE_FAILED = "I couldn't save that just now. Please try again in a few minutes."
STORAGE_FULL = "I can't take any more messages from you today. Please try again tomorrow."
STORE_PER_DAY = 200  # messages kept per sender per day: flooding storage is still bounded
FALLBACK = "I can't chat right now, but I've passed your message on. If you like, add your name and how to reach you."
PAUSED = "I'm paused right now, but I've passed your message on."
NOTE_EVERY = timedelta(minutes=15)
URGENT_RANKS = ("urgent", "unverified")
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


def _resume_text(session: Session, sender: Sender, rs: ReceptionSettings, now: datetime, hour: int, day: int) -> str:
    """When the sender can expect an answer again, as "in about 40 minutes": the oldest message still inside the
    window that was exceeded, plus the window. No clock time, because the sender's time zone is not known."""

    def reset(window: timedelta) -> datetime:
        oldest = session.scalar(
            select(func.min(Event.created_at)).where(
                Event.direction == "in",
                Event.sender_identity_id == sender.identity.id,
                Event.created_at >= now - window,
            )
        )
        return (oldest or now) + window

    ends = []
    if hour > rs.per_hour:
        ends.append(reset(timedelta(hours=1)))
    if day > rs.per_day:
        ends.append(reset(timedelta(hours=24)))
    minutes = max(1, -(-int((max(ends) - now).total_seconds()) // 60)) if ends else 60
    if minutes < 90:
        return f"in about {minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    return f"in about {hours} hours"


def _stored_today(session: Session, sender: Sender, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ReceptionMessage)
            .where(
                ReceptionMessage.identity_id == sender.identity.id,
                ReceptionMessage.created_at >= now - timedelta(hours=24),
            )
        )
        or 0
    )


def _keep(
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
) -> ReceptionMessage | None:
    """Store the message, or None when that failed: nothing may then be said to be passed on. A savepoint, so a
    failure inside the owner's note or the triage item rolls the whole row back instead of half-writing it."""
    try:
        with session.begin_nested():
            return store_message(agent, session, owner, sender, ctx, msg, inbound, taken, source=source)
    except Exception:
        log.exception("receptionist could not store a message")
        audit(session, "reception", "store_failed", identity_id=sender.identity.id)
        return None


def _resumed(session: Session, ctx: Context) -> bool:
    """The last thing Ava said about her limit here was the limit notice, and she has not said she is back since."""

    def latest(mark: str) -> datetime | None:
        return session.scalar(
            select(func.max(Event.created_at)).where(
                Event.context_id == ctx.id, Event.direction == "out", Event.content.contains(mark)
            )
        )

    noticed, back = latest(LIMIT_MARK), latest(RESUMED)
    return noticed is not None and (back is None or noticed > back)


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
    exhausted = hour > rs.per_hour or day > rs.per_day
    first = _first_reply(session, ctx)
    hello = greeting(agent.name, owner, rs)
    over_storage = _stored_today(session, sender, now) >= STORE_PER_DAY

    if exhausted:  # no model call: code keeps the message and says, once, when Ava will answer again
        audit(session, "reception", "rate_limited", identity_id=sender.identity.id, hour=hour, day=day)
        if over_storage:
            return "" if _said_recently(session, ctx, STORAGE_FULL, now) else STORAGE_FULL
        row = _keep(
            agent, session, owner, sender, ctx, msg, inbound, rturn.TakenMessage(message=msg.text[:2000]), source="code"
        )
        if row is None:
            return SAVE_FAILED
        window = now - timedelta(hours=24 if day > rs.per_day else 1)
        said = session.scalar(
            select(Event.id)
            .where(
                Event.context_id == ctx.id,
                Event.direction == "out",
                Event.content.contains(LIMIT_MARK),
                Event.created_at >= window,
            )
            .limit(1)
        )
        return "" if said is not None else LIMIT_REPLY.format(when=_resume_text(session, sender, rs, now, hour, day))

    if over_storage:  # a sender who floods storage is told so; nothing is claimed as passed on
        audit(session, "reception", "storage_cap", identity_id=sender.identity.id)
        return "" if _said_recently(session, ctx, STORAGE_FULL, now) else STORAGE_FULL

    resumed = _resumed(session, ctx)
    reply, from_model, mode = _talk(agent, session, owner, rs, ctx, msg)
    # Code stores every message; the model only adds structure. One it did not structure is kept as typed.
    structured = (
        mode == "model" and from_model is not None and bool(from_model.message.strip() or from_model.topic.strip())
    )
    taken = from_model if structured and from_model is not None else rturn.TakenMessage(message=msg.text[:2000])
    row = _keep(agent, session, owner, sender, ctx, msg, inbound, taken, source="model" if structured else "code")
    if row is None:  # never "I've passed it on" for something that was not stored
        reply = SAVE_FAILED
    if first:  # the notice is code's job, not the model's: always, and only once per conversation
        reply = f"{hello} {reply}"
        if row is not None:
            reply = f"{reply}\n\n{CAP_NOTICE.format(n=rs.per_hour)}"
    elif row is not None:
        left = rs.per_hour - hour
        near_at = max(1, -(-rs.per_hour * 4 // 5))  # 80 percent, rounded up
        if hour == near_at and left > 0 and not _said_recently(session, ctx, NEAR_MARK, now):
            reply = f"{reply}\n\n{NEAR_NOTICE.format(left=left, s='' if left == 1 else 's')}"
    if resumed:
        reply = f"{RESUMED} {reply}"
    if claimed:
        reply = f"{reply}\n\n{CLAIM_NOTE}"
    taken_ok = row is not None
    audit(session, "reception", "turn", identity_id=sender.identity.id, mode=mode, took_message=taken_ok)
    return reply


def _talk(
    agent: Any, session: Session, owner: Person, rs: ReceptionSettings, ctx: Context, msg: InboundMessage
) -> tuple[str, rturn.TakenMessage | None, str]:
    """(reply, taken message, mode): mode is model | code (no model, budget, bad answer) | paused."""
    from superaide.brain.spend.budget import AgentFrozen, BudgetExceeded
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
    from superaide.memory.knowledge.store import preferences_for

    if not text.strip():
        return None
    found = find_moment(text, now.astimezone(zone(preferences_for(session, owner).timezone)))
    return found[0].date() if found and found[0] > now else None


def _first_name(row: ReceptionMessage) -> str:
    """The first word of the name the sender gave, letters only. It is their own unverified claim, so the notice
    shows it as that (`Meera, someone new,`), and nothing else they typed (#516)."""
    word = (row.name.split() or [""])[0]
    return "".join(c for c in word if c.isalpha() or c in "'-")[:20].strip("'-")


def _local_time(session: Session, owner: Person, row: ReceptionMessage) -> str:
    from superaide.intentions.when import zone
    from superaide.memory.knowledge.store import preferences_for

    at = row.created_at or datetime.now(UTC)
    return at.astimezone(zone(preferences_for(session, owner).timezone)).strftime("%H:%M")


def _reason(session: Session, owner: Person, row: ReceptionMessage) -> str:
    """The owner's notice: who (a first name only if they gave one), where and when. Never any of the message."""
    where = CHANNEL.get(row.provider, row.provider)
    name = _first_name(row)
    who = f"{name}, someone new," if name else "someone new"
    when = f"{where} at {_local_time(session, owner, row)}"
    if row.rank == "unverified":
        return f"Message from {who} on {when} asks for something risky (money, codes or secrecy)"
    if row.rank == "urgent":
        return f"Message from {who} on {when} may be urgent"
    return f"Message taken from {who} on {when}"


def _to_triage(session: Session, owner: Person, row: ReceptionMessage, inbound: Event, now: datetime) -> None:
    """With triage on: a Needs-you item. Never `interrupt` (a stranger isn't verified): at most an
    unverified request that escalates, when it matches one of the owner's urgent topics."""
    from superaide.memory.knowledge.profile import in_quiet_hours
    from superaide.memory.knowledge.store import preferences_for
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
        rule_hits=["stranger", *row.rule_hits], decision=str(decision), reason=_reason(session, owner, row),
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

    # A burst makes one note. An urgent or risky message is judged against earlier urgent ones only, so a flood of
    # ordinary messages (or the cap notice, #474) cannot hide it from the owner.
    quiet = (
        select(func.count())
        .select_from(ReceptionMessage)
        .where(ReceptionMessage.id != row.id, ReceptionMessage.created_at >= now - NOTE_EVERY)
    )
    if row.rank in URGENT_RANKS:
        quiet = quiet.where(ReceptionMessage.rank.in_(URGENT_RANKS))
    if session.scalar(quiet):
        return
    text = _reason(session, owner, row) + ". Open your dashboard to read it."
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
