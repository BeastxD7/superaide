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
from superaide.presence import api as presence
from superaide.reception import ask, card, signals
from superaide.reception import turn as rturn
from superaide.reception.models import ReceptionMessage, ReceptionSettings

log = logging.getLogger(__name__)

PROVIDERS = ("telegram", "slack")
GREETING_START = "Hi, I'm "  # greeting() always opens with this
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
    "I've reached my reply limit for now, so I'll stop replying and can reply again {when}. "
    "What you send meanwhile is still passed on."
)
RESUMED = "I can reply again."
SAVE_FAILED = "I couldn't save that just now. Please try again in a few minutes."
STORAGE_FULL = "I can't take any more messages from you today. Please try again tomorrow."
STORE_PER_DAY = 200  # messages kept per sender per day: flooding storage is still bounded
FALLBACK = (
    "I can't chat right now, but I've passed your message on, and it will be waiting when they next check in. "
    "If you like, add your name and how to reach you."
)
PAUSED = "I'm paused right now, but I've passed your message on."
# Safety replies (#498): fixed, reviewed text. Code picks one from the labels in reception/signals.py; the model
# never writes them and is never told what was detected. Each says it is an AI, gives no advice, and promises
# only what code does (the message is stored and shown to the owner). The marks are what `_said_recently` finds.
DISTRESS_MARK = "I'm really sorry you're going through this"
DISTRESS_REPLY = (
    f"{DISTRESS_MARK}. I'm an AI assistant, so I can't give you the help you deserve, but I've passed your message "
    "on right away. If you might be in danger or thinking of hurting yourself, please call your local emergency "
    "number now, or find a crisis line for your country at findahelpline.com. You don't have to face this alone."
)
THREAT_MARK = "I've passed this on to the person you wrote to"
THREAT_REPLY = (
    f"{THREAT_MARK}. I'm an AI assistant and can't act on it myself. If anyone is in immediate danger, please call "
    "your local emergency number."
)
REFUSAL_MARK = "I can't share or help with that"
REFUSAL_REPLY = f"{REFUSAL_MARK}. I'm an AI assistant, and I've passed your message on instead."
BOUNDARY_MARK = "I'm here to pass on messages"
BOUNDARY_REPLY = (
    f"I'm an AI assistant. {BOUNDARY_MARK}, and I won't continue this conversation in that tone. "
    "What you wrote is still passed on."
)
MODEL_LIMIT_REPLY = (
    "I've passed that on. I can't answer every message today, but anything else you send is still passed on."
)
MODEL_TURNS_PER_DAY = 20  # receptionist model turns one unknown sender can cause a day; after that code answers alone
DISTRESS_REPLIES_PER_HOUR = 3  # a person in distress is answered again, but a repeated phrase cannot run up replies
SEVERE_NOTES_PER_HOUR = 6  # owner notes one sender can cause in an hour for distress or a threat
SEVERE_NOTE_EVERY = timedelta(minutes=5)
NOTE_EVERY = timedelta(minutes=15)
NOTES_PER_HOUR = 3  # owner notifications one sender can cause in an hour: code's cap, whatever the message says (#477)
URGENT_RANKS = ("urgent", "unverified")
CHANNEL = {"telegram": "Telegram", "slack": "Slack"}


def settings_for(session: Session, owner: Person) -> ReceptionSettings:
    row = session.scalar(select(ReceptionSettings).where(ReceptionSettings.person_id == owner.id))
    if row is None:
        row = ReceptionSettings(person_id=owner.id)
        session.add(row)
        session.flush()
    return row


def greeting(agent_name: str, owner: Person, rs: ReceptionSettings, *, has_card: bool = False) -> str:
    """The EU AI Act notice: who they're talking to, and what she can really do (issue #475). The owner's first
    name only if they allowed it. Built in code from what is configured, never from the model."""
    name = rturn.first_name(owner) if rs.show_first_name else ""
    who = f"Hi, I'm {agent_name}, {name}'s AI assistant." if name else f"Hi, I'm {agent_name}, an AI assistant."
    can = "I can share what's been made public and take a message." if has_card else "I can take a message."
    return f"{who} {can}"


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


def _said_count(session: Session, ctx: Context, text: str, since: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.context_id == ctx.id,
                Event.direction == "out",
                Event.content.contains(text),
                Event.created_at >= since,
            )
        )
        or 0
    )


def _model_turns(session: Session, sender: Sender, now: datetime) -> int:
    """Receptionist model turns for this sender in the last 24 hours (code writes one `turn` audit row per turn)."""
    from superaide.platform.db.models import AuditLog

    return int(
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "turn",
                AuditLog.actor == "reception",
                AuditLog.created_at >= now - timedelta(hours=24),
                AuditLog.detail["identity_id"].astext == str(sender.identity.id),
                AuditLog.detail["mode"].astext == "model",
            )
        )
        or 0
    )


def _first_reply(session: Session, ctx: Context) -> bool:
    """No greeting yet in this conversation. A fixed safety reply (#498) is not a greeting, so a thread that opened
    with one still gets the introduction and the limit notice on its first ordinary turn."""
    return (
        session.scalar(
            select(Event.id)
            .where(Event.context_id == ctx.id, Event.direction == "out", Event.content.startswith(GREETING_START))
            .limit(1)
        )
        is None
    )


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
    sig: signals.Signals = signals.NONE,
) -> ReceptionMessage | None:
    """Store the message, or None when that failed: nothing may then be said to be passed on. A savepoint, so a
    failure inside the owner's note or the triage item rolls the whole row back instead of half-writing it."""
    try:
        with session.begin_nested():
            return store_message(agent, session, owner, sender, ctx, msg, inbound, taken, source=source, sig=sig)
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
    if msg.attachments and not msg.text.strip():  # a file alone: read only if the owner chose a service (#501, #511)
        from superaide.reception import media as visitor_media

        return visitor_media.handle(agent, session, owner, sender, ctx, msg, inbound)
    now = datetime.now(UTC)
    claimed = linking.detect_claimed_name(msg.text)
    if claimed:
        agent._claim(session, sender, claimed)  # the linking challenge, exactly as before
    hour, day = _counts(session, sender, now)
    exhausted = hour > rs.per_hour or day > rs.per_day
    first = _first_reply(session, ctx)
    hello = greeting(agent.name, owner, rs, has_card=rturn.has_card(session, owner, rs))
    over_storage = _stored_today(session, sender, now) >= STORE_PER_DAY
    sig = signals.detect(msg.text)
    if exhausted:
        sig = sig.with_(signals.FLOOD)

    if sig and not over_storage and (sig.has(*signals.SEVERE, signals.HARASSMENT) or sig.wants_data):
        return _safety_reply(agent, session, owner, sender, ctx, msg, inbound, sig, now)

    if exhausted:  # no model call: code keeps the message and says, once, when Ava will answer again
        audit(session, "reception", "rate_limited", identity_id=sender.identity.id, hour=hour, day=day)
        if over_storage:
            return "" if _said_recently(session, ctx, STORAGE_FULL, now) else STORAGE_FULL
        row = _keep(
            agent,
            session,
            owner,
            sender,
            ctx,
            msg,
            inbound,
            rturn.TakenMessage(message=msg.text[:2000]),
            source="code",
            sig=sig,
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
    if _model_turns(session, sender, now) >= MODEL_TURNS_PER_DAY:  # this sender's share of the model is spent
        reply, from_model, mode, asked, sched = MODEL_LIMIT_REPLY, None, "code", None, None
        sig = sig.with_(signals.FLOOD)
    else:
        reply, from_model, mode, asked, sched = _talk(agent, session, owner, rs, ctx, msg)
    # Code stores every message; the model only adds structure. One it did not structure is kept as typed.
    structured = (
        mode == "model" and from_model is not None and bool(from_model.message.strip() or from_model.topic.strip())
    )
    taken = from_model if structured and from_model is not None else rturn.TakenMessage(message=msg.text[:2000])
    row = _keep(
        agent, session, owner, sender, ctx, msg, inbound, taken, source="model" if structured else "code", sig=sig
    )
    if row is None:  # never "I've passed it on" for something that was not stored
        reply = SAVE_FAILED
    elif asked is not None and asked.question.strip():  # #494: what she says follows what code did, not the model
        opened = _ask(session, owner, sender, ctx, msg, asked.question)
        reply = ask.HOLD.format(who=ask.owner_ref(session, owner)) if opened else ask.REFUSED
    elif sched is not None:  # #496: the times and the words are code's; the model only said they want to meet
        reply = _schedule(session, owner, sender, ctx, msg, sched, from_model.name if from_model else "")
    if first and not rturn.has_card(session, owner, rs):  # she could only take a message: say so to the owner (#475)
        try:
            with session.begin_nested():
                nudge_owner(session, owner)
        except Exception:
            log.warning("empty card notice failed", exc_info=True)
    if first:  # the notice is code's job, not the model's: always, and only once per conversation
        reply = f"{hello} {reply}"
        if row is not None:
            reply = f"{reply}\n\n{CAP_NOTICE.format(n=rs.per_hour)}"
    elif row is not None:
        left = rs.per_hour - hour
        near_at = max(1, -(-rs.per_hour * 4 // 5))  # 80 percent, rounded up
        if hour == near_at and left > 0 and not _said_recently(session, ctx, NEAR_MARK, now):
            reply = f"{reply}\n\n{NEAR_NOTICE.format(left=left, s='' if left == 1 else 's')}"
    if row is not None:  # the owner's declared status: the only availability words a visitor ever hears (#495)
        state = presence.active(session, owner.id)
        if state is not None and state.visitor_line and not _said_recently(session, ctx, state.visitor_line, now):
            reply = f"{reply}\n\n{state.visitor_line}"
    if resumed:
        reply = f"{RESUMED} {reply}"
    if claimed:
        reply = f"{reply}\n\n{CLAIM_NOTE}"
    taken_ok = row is not None
    audit(session, "reception", "turn", identity_id=sender.identity.id, mode=mode, took_message=taken_ok)
    return reply


def _safety_reply(
    agent: Any,
    session: Session,
    owner: Person,
    sender: Sender,
    ctx: Context,
    msg: InboundMessage,
    inbound: Event,
    sig: signals.Signals,
    now: datetime,
) -> str:
    """The reply when code found distress, a threat, abuse, or a request for credentials, money or private details
    (#498). No model call, so nothing the stranger wrote can steer the answer, and no lookup, so the refusal is the
    same sentence whether or not the owner has the data they ask for (nobody can probe). The message is always
    stored first, and the owner is told in the right tone; what the stranger gets is one of the fixed texts here."""
    row = _keep(
        agent, session, owner, sender, ctx, msg, inbound, rturn.TakenMessage(message=msg.text[:2000]), source="code",
        sig=sig,
    )  # fmt: skip
    if row is None:
        return SAVE_FAILED
    audit(session, "reception", "safety_reply", identity_id=sender.identity.id, labels=list(sig.labels))
    hour, day = now - timedelta(hours=1), now - timedelta(hours=24)
    if sig.has(signals.DISTRESS):  # a person is answered again, a few times an hour
        text = DISTRESS_REPLY
        left = DISTRESS_REPLIES_PER_HOUR - _said_count(session, ctx, DISTRESS_MARK, hour)
    elif sig.has(signals.THREAT):
        text, left = THREAT_REPLY, 1 - _said_count(session, ctx, THREAT_MARK, hour)
    elif sig.wants_data:
        text, left = REFUSAL_REPLY, 1 - _said_count(session, ctx, REFUSAL_MARK, hour)
    else:  # abuse: one calm boundary a day, then silence on that thread; the message is still passed on
        text, left = BOUNDARY_REPLY, 1 - _said_count(session, ctx, BOUNDARY_MARK, day)
    return text if left > 0 else ""


def _ask(session: Session, owner: Person, sender: Sender, ctx: Context, msg: InboundMessage, question: str) -> bool:
    """Open the owner's question (a savepoint: a failure leaves the visitor's message stored and says no)."""
    try:
        with session.begin_nested():
            return (
                ask.open_question(
                    session, owner, identity_id=sender.identity.id, ctx=ctx, provider=msg.provider, question=question
                )
                is not None
            )
    except Exception:
        log.exception("receptionist could not open a question")
        return False


def _schedule(
    session: Session,
    owner: Person,
    sender: Sender,
    ctx: Context,
    msg: InboundMessage,
    sched: rturn.ScheduleAsk,
    name: str,
) -> str:
    """Offer or confirm meeting times (a savepoint: a failure leaves the message stored and says no)."""
    from superaide.reception import booking

    try:
        with session.begin_nested():
            return booking.handle(
                session, owner, identity_id=sender.identity.id, ctx=ctx, provider=msg.provider, ask=sched,
                visitor_name=name,
            )  # fmt: skip
    except Exception:
        log.exception("receptionist could not handle a booking")
        return booking.UNAVAILABLE


def _talk(
    agent: Any, session: Session, owner: Person, rs: ReceptionSettings, ctx: Context, msg: InboundMessage
) -> tuple[str, rturn.TakenMessage | None, str, rturn.AskOwner | None, rturn.ScheduleAsk | None]:
    """(reply, taken message, mode, a question the model proposed, a meeting the model proposed): mode is model |
    code (no model, budget, bad answer) | paused."""
    from superaide.brain.spend.budget import AgentFrozen, BudgetExceeded
    from superaide.brain.turn import build_messages
    from superaide.brain.wiring import brain_ready, with_credits
    from superaide.memory.store import recent_events

    if not brain_ready(agent.settings):
        return FALLBACK, None, "code", None, None
    from superaide.reception import resilience

    system = rturn.system_prompt(session, owner, rs, agent.name)
    messages = build_messages(recent_events(session, ctx, limit=rturn.HISTORY), msg.text)
    try:
        out, _cost = rturn.run(agent.llm, model=with_credits(agent.settings).model_triage, system=system,
                               messages=messages)  # fmt: skip
    except BudgetExceeded as exc:
        audit(session, "reception", "budget_cap_reached", period=exc.period)
        resilience.record_model(session, resilience.BUDGET, exc.period)
        return FALLBACK, None, "code", None, None
    except AgentFrozen:
        return PAUSED, None, "paused", None, None
    except Exception:
        log.exception("receptionist turn failed")
        resilience.record_model(session, resilience.ERROR, "turn_failed")
        return FALLBACK, None, "code", None, None
    if out is None or not out.reply.strip():
        resilience.record_model(session, resilience.ERROR, "unreadable")
        return FALLBACK, None, "code", None, None
    resilience.record_model(session, resilience.OK)
    return out.reply.strip()[:1500], out.take_message, "model", out.ask_owner, out.schedule


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
    sig: signals.Signals = signals.NONE,
) -> ReceptionMessage:
    from superaide.asklink.service import rank_request

    now = datetime.now(UTC)
    arrived = inbound.created_at or now  # "within 5 minutes" counts from when they wrote it, not from now
    deadline_at, parse = _deadline(session, owner, taken.deadline_text, arrived)
    r = rank_request(
        session, owner, topic=taken.topic, why=taken.message, deadline=None, deadline_at=deadline_at, now=arrived
    )
    rank = r.rank
    if sig.wants_data:
        rank = (
            "unverified"  # asking for data or money, or for private details in someone's name: never a normal message
        )
    elif sig.has(*signals.SEVERE) and rank in ("normal", "low"):
        rank = "urgent"
    handle = f"@{msg.sender_username}" if msg.sender_username else msg.sender_display
    row = ReceptionMessage(
        identity_id=sender.identity.id, context_id=ctx.id, inbound_event_id=inbound.id, provider=msg.provider,
        handle=handle[:200], name=taken.name[:120], contact=taken.contact[:200], topic=taken.topic[:200],
        message=taken.message[:4000], deadline_text=taken.deadline_text[:120], claimed_urgency=taken.urgency,
        deadline_at=deadline_at, deadline_parse=parse, rank=rank,
        rule_hits=[h for h in r.hits if h != "source:ask_link"] + [f"source:{msg.provider}", *sig.hits()],
        source=source,
    )  # fmt: skip
    session.add(row)
    session.flush()
    audit(session, "reception", "message_taken", message_id=row.id, rank=row.rank, source=source)
    if sig:  # labels only, never a quote (#498)
        audit(session, "reception", "signals", message_id=row.id, labels=list(sig.labels))
    if agent.settings.triage_enabled:
        _to_triage(session, owner, row, inbound, now)
        if sig.has(*signals.SEVERE):  # distress or a threat is also told at once, not only held in Needs you (#498)
            _note(session, owner, row, now)
    else:
        _note(session, owner, row, now)
    return row


def _deadline(session: Session, owner: Person, text: str, arrived: datetime) -> tuple[datetime | None, str]:
    """(the moment, how it was read): the full datetime in UTC, anchored on `arrived`, the time the message came.
    "" when no deadline was given; "unknown" when the words hold no time code can read (the owner still sees the
    words); "past" when the time is already gone."""
    from superaide.intentions.when import find_moment, zone
    from superaide.memory.knowledge.store import preferences_for

    if not text.strip():
        return None, ""
    found = find_moment(text, arrived.astimezone(zone(preferences_for(session, owner).timezone)))
    if found is None:
        return None, "unknown"
    moment = found[0].astimezone(UTC)
    return (moment, "ok") if moment > arrived else (None, "past")


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
    sig = signals.Signals(signals.from_hits(row.rule_hits))
    if sig.has(signals.DISTRESS):
        return f"Message from {who} on {when} may be from someone in distress. Please read it now"
    if sig.has(signals.THREAT):
        return f"Message from {who} on {when} contains what looks like a threat. Please read it now"
    if sig.wants_data:
        return (
            f"Message from {who} on {when} asks for private details, codes or money, and may claim to speak for "
            "you. I shared nothing. It may be a scam"
        )
    if sig.has(signals.HARASSMENT):
        return f"Message from {who} on {when} is abusive"
    if row.rank == "unverified":
        return f"Message from {who} on {when} asks for something risky (money, codes or secrecy)"
    if row.rank == "urgent":
        return f"Message from {who} on {when} may be urgent"
    return f"Message taken from {who} on {when}"


def _sandbox() -> Any:
    from superaide.platform.sandbox_guard import active

    return active()


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
    if escalate and (sandbox := _sandbox()) is not None:  # a preview (#499): the ladder is described, never started
        sandbox.skipped.append("escalation: the ladder that rings the owner is not started from a preview")
    elif escalate:
        pref = preferences_for(session, owner)
        quiet_end = next_local(now, pref.timezone, pref.quiet_end) if in_quiet_hours(pref, now) else None
        open_escalation(session, d, owner, max_step=1, breaks_quiet=False, defer_until=quiet_end,
                        deadline_at=row.deadline_at, now=now)  # fmt: skip


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
    """With triage off: one short in-app note, built from fields code read (first name, channel, time, deadline),
    never the stranger's words. A burst makes one note (15 minutes, per sender); one sender can cause at most
    NOTES_PER_HOUR notes an hour, and urgency lifts that only when the owner chose so. In quiet hours the note stays
    in the app and nothing is pushed to Telegram or Slack."""
    from superaide.memory.knowledge.profile import in_quiet_hours
    from superaide.memory.knowledge.store import preferences_for
    from superaide.proactive.notify import notify_owner

    # An urgent or risky message is judged against earlier urgent ones only, so a flood of ordinary messages (or
    # the cap notice, #474) cannot hide it from the owner.
    severe = bool({signals.PREFIX + s for s in signals.SEVERE} & set(row.rule_hits or []))
    burst = (
        select(func.count())
        .select_from(ReceptionMessage)
        .where(
            ReceptionMessage.id != row.id,
            ReceptionMessage.identity_id == row.identity_id,  # per sender: one chatty person cannot hide another (#472)
            ReceptionMessage.created_at >= now - (SEVERE_NOTE_EVERY if severe else NOTE_EVERY),
        )
    )
    if severe:  # distress or a threat: only an earlier one counts, so ordinary chatter never hides it
        burst = burst.where(ReceptionMessage.rule_hits.overlap([signals.PREFIX + s for s in signals.SEVERE]))
    elif row.rank in URGENT_RANKS:
        burst = burst.where(ReceptionMessage.rank.in_(URGENT_RANKS))
    if session.scalar(burst):
        return
    sent_this_hour = session.scalar(
        select(func.count())
        .select_from(ReceptionMessage)
        .where(
            ReceptionMessage.identity_id == row.identity_id,
            ReceptionMessage.notified_at >= now - timedelta(hours=1),
        )
    )
    if (sent_this_hour or 0) >= (SEVERE_NOTES_PER_HOUR if severe else NOTES_PER_HOUR) and not (
        row.rank in URGENT_RANKS and settings_for(session, owner).urgent_over_cap
    ):
        audit(session, "reception", "note_capped", message_id=row.id, rank=row.rank)
        return
    text = _reason(session, owner, row) + _reply_by(session, owner, row) + ". Open your dashboard to read it."
    pref = preferences_for(session, owner)
    row.notified_at = now
    # The note is code text (a reason, never the stranger's words), so it may follow the owner's interrupt preference
    # like any other notification: someone who asked for Telegram is not left with an unread in-app line (#491).
    # A stranger never breaks quiet hours, and while the owner's declared status is on nothing is pushed (#495), except
    # distress or a threat when the owner turned on urgent_over_cap (#498).
    meta = {"kind": "message_taken", "message_id": str(row.id), "source": f"notification:{row.id}"}
    held = in_quiet_hours(pref, now) or presence.active(session, owner.id, now) is not None
    if held and severe and settings_for(session, owner).urgent_over_cap:
        held = False
    notify_owner(session, owner, text, meta, channel="app" if held else "default")


def _reply_by(session: Session, owner: Person, row: ReceptionMessage) -> str:
    """ ", they asked for a reply by 14:07": the deadline code read from what they said, as their claim."""
    from superaide.intentions.when import zone
    from superaide.memory.knowledge.store import preferences_for

    if row.deadline_parse != "ok" or row.deadline_at is None:
        return ""
    local = row.deadline_at.astimezone(zone(preferences_for(session, owner).timezone))
    return f", and they asked for a reply by {local:%H:%M}"


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


# --- the share card (issue #475) -------------------------------------------------------------------------------


def card_preferences(session: Session, owner: Person) -> Any:
    """The owner's preference row, which holds the card values (created on first use)."""
    from superaide.memory.knowledge.store import preferences_for

    return preferences_for(session, owner)


def share_card_state(session: Session) -> tuple[bool, list[str]] | None:
    """(is the first name shared, the card lines she may say) for the owner of this instance, or None without one.
    Read by the owner chat's capability block, so what Ava says about the card is what the database holds."""
    from superaide.app.accounts import owner as instance_owner

    boss = instance_owner(session)
    if boss is None:
        return None
    rs = settings_for(session, boss)
    return bool(rs.show_first_name and rturn.first_name(boss)), card.lines(card.preference(session, boss))


def first_name_shared(session: Session, owner: Person) -> bool:
    return bool(settings_for(session, owner).show_first_name)


def set_first_name_shared(session: Session, owner: Person, on: bool) -> None:
    """The owner's choice to let Ava say their first name to someone she doesn't know (onboarding's share step and the
    receptionist settings write the same switch, so there is one truth). Off until chosen."""
    rs = settings_for(session, owner)
    if rs.show_first_name != on:
        rs.show_first_name = on
        audit(session, "reception", "first_name_switch", on=on)


def nudge_owner(session: Session, owner: Person) -> bool:
    """A stranger wrote and the card is empty, so Ava could only take a message: tell the owner once a week, in the
    app, with no word of what the stranger said (the owner's chat history is read by the model)."""
    from superaide.platform.db.models import AuditLog
    from superaide.proactive.notify import notify_owner

    since = datetime.now(UTC) - card.NUDGE_EVERY
    recent = session.scalar(
        select(AuditLog.id).where(AuditLog.action == "card_empty_nudge", AuditLog.created_at > since).limit(1)
    )
    if recent is not None:
        return False
    audit(session, "reception", "card_empty_nudge")
    notify_owner(
        session,
        owner,
        "Someone I don't know wrote to me and asked about you. Your share card is empty, so all I could do was take "
        f"a message. To let me say something (your company, role or city), fill it in under {card.PLACE}.",
        {"kind": "card_empty", "path": card.SETTINGS_PATH},
        channel="app",
    )
    return True
