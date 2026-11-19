"""The ask-the-owner loop (#494): a visitor asks something the public card does not answer.

Instead of a dead end ("I can't share that"), Ava can put the question to the owner, who answers it in the app; code
then relays the owner's own words to the visitor. As everywhere in reception, the model only proposes
(`ReceptionTurn.ask_owner`); code decides:

- Whether a question is opened: not about secrets (passwords, codes, money, accounts), at most 3 a day and one open
  at a time per visitor, 20 open in all. If code refuses, the visitor is told so plainly and nothing is queued.
- What the visitor is told: a fixed sentence either way. The model's own reply is dropped when it asked, so Ava can
  never promise "I'll check" for a question that was not opened.
- What the owner is pinged with: fixed text, never the visitor's words (the owner's chat model reads notifications).
  The question itself is shown quoted in the app. Quiet hours, the owner's status and the 3-an-hour cap per visitor
  decide whether the ping is pushed or stays in the app.
- What the visitor hears back: the owner's typed answer inside a fixed template, sent to the conversation the
  question came from, once. Nothing is saved as knowledge here: the answer is for this person only.

Not here yet (see the PR): saving an answer as an FAQ for an audience, the visitor's "still pending" note after a
timeout, and the offline eval file."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.identity.models import Person
from superaide.memory.audience import Audience
from superaide.memory.models import Context
from superaide.platform.audit import audit
from superaide.reception.inbox import CHANNEL, plain
from superaide.reception.models import ReceptionMessage, VisitorQuestion

log = logging.getLogger(__name__)

PER_DAY = 3  # questions one visitor can open in 24 hours
OPEN_CAP = 20  # questions waiting for the owner, all visitors
PINGS_PER_HOUR = 3  # same cap as message notes (reception/service.py NOTES_PER_HOUR)
EXPIRES = timedelta(hours=24)  # an answer to a question this old is not relayed
QUESTION_CHARS = 200
ANSWER_CHARS = 500
SETTINGS_PATH = "/dashboard/settings"

# Things a visitor must never be asked on the owner's behalf, whatever the model proposes.
SECRETS = re.compile(
    r"pass(word|code)|\botp\b|one[- ]time|verification code|\bpin\b|\bcvv\b|card number|account number|\biban\b|"
    r"\bssn\b|social security|passport|seed phrase|private key|api key|\btoken\b|log ?in|credentials|bank|"
    r"\bsend (me )?money\b|\bwire\b|\bpayment\b",
    re.IGNORECASE,
)

HOLD = (
    "I don't have that information. I've asked {who} and I'll pass the answer on here if I get one. "
    "Your message is saved too."
)
REFUSED = "I can't share that, and I can't ask about it. I can take a message if you like."
TEMPLATE = "About your question “{question}”: here is the answer I was given. {answer}"
DECLINED = "I couldn't get an answer to that, sorry. If you like, leave a message and I'll pass it on."


def owner_ref(session: Session, owner: Person) -> str:
    from superaide.reception import service
    from superaide.reception import turn as rturn

    first = rturn.first_name(owner)
    return first if first and service.settings_for(session, owner).show_first_name else "the person I work for"


def refusal(session: Session, identity_id: uuid.UUID, question: str, now: datetime) -> str:
    """Why no question is opened ("" when one may be). Code's decision, whatever the model asked."""
    if not question:
        return "empty"
    if SECRETS.search(question):
        return "secret"
    today = session.scalar(
        select(func.count())
        .select_from(VisitorQuestion)
        .where(VisitorQuestion.identity_id == identity_id, VisitorQuestion.created_at >= now - timedelta(hours=24))
    )
    if (today or 0) >= PER_DAY:
        return "visitor_cap"
    waiting = session.scalar(
        select(VisitorQuestion.id)
        .where(VisitorQuestion.identity_id == identity_id, VisitorQuestion.state == "asked")
        .limit(1)
    )
    if waiting is not None:
        return "one_open"
    total = session.scalar(select(func.count()).select_from(VisitorQuestion).where(VisitorQuestion.state == "asked"))
    if (total or 0) >= OPEN_CAP:
        return "open_cap"
    return ""


def open_question(
    session: Session, owner: Person, *, identity_id: uuid.UUID, ctx: Context, provider: str, question: str
) -> VisitorQuestion | None:
    """Opens one question and pings the owner, or returns None when code refuses (audited with the reason)."""
    now = datetime.now(UTC)
    clean = plain(question, QUESTION_CHARS)
    why = refusal(session, identity_id, clean, now)
    if why:
        audit(session, "reception", "question_refused", identity_id=identity_id, reason=why)
        return None
    row = VisitorQuestion(identity_id=identity_id, context_id=ctx.id, provider=provider, question=clean)
    session.add(row)
    session.flush()
    audit(session, "reception", "question_asked", question_id=row.id, provider=provider)
    _ping(session, owner, row, now)
    return row


def _ping(session: Session, owner: Person, row: VisitorQuestion, now: datetime) -> None:
    """Fixed text only. Pushed to the owner's channel unless it is quiet hours, their status is on, or this visitor
    already caused 3 pings this hour (then it waits in the app, where the question is listed)."""
    from superaide.memory.knowledge.profile import in_quiet_hours
    from superaide.memory.knowledge.store import preferences_for
    from superaide.presence import api as presence
    from superaide.proactive.notify import notify_owner

    hour_ago = now - timedelta(hours=1)
    pings = (
        session.scalar(
            select(func.count())
            .select_from(VisitorQuestion)
            .where(
                VisitorQuestion.identity_id == row.identity_id,
                VisitorQuestion.id != row.id,
                VisitorQuestion.notified_at >= hour_ago,
            )
        )
        or 0
    ) + (
        session.scalar(
            select(func.count())
            .select_from(ReceptionMessage)
            .where(ReceptionMessage.identity_id == row.identity_id, ReceptionMessage.notified_at >= hour_ago)
        )
        or 0
    )
    if pings >= PINGS_PER_HOUR:
        audit(session, "reception", "question_ping_capped", question_id=row.id)
        return
    pref = preferences_for(session, owner)
    held = in_quiet_hours(pref, now) or presence.active(session, owner.id, now) is not None
    where = CHANNEL.get(row.provider, row.provider)
    text = (
        f"Someone I don't know asked me something on {where} that I couldn't answer. "
        f"Open Settings to read the question and answer it: {SETTINGS_PATH}"
    )
    row.notified_at = now
    meta = {"kind": "visitor_question", "question_id": str(row.id), "source": f"notification:{row.id}"}
    notify_owner(session, owner, text, meta, channel="app" if held else "default")


def expire_old(session: Session, now: datetime | None = None) -> None:
    """Questions nobody answered in 24 hours become `expired`; their answer is no longer relayed."""
    now = now or datetime.now(UTC)
    for q in session.scalars(
        select(VisitorQuestion).where(VisitorQuestion.state == "asked", VisitorQuestion.created_at < now - EXPIRES)
    ):
        q.state = "expired"
        audit(session, "reception", "question_expired", question_id=q.id)


def _send(session: Session, q: VisitorQuestion, text: str, *, kind: str) -> bool:
    """Send `text` to the conversation the question came from and record it. True when the platform took it."""
    from superaide.channels.health import DeliveryFailed
    from superaide.memory.store import log_event
    from superaide.proactive import notify

    ctx = session.get(Context, q.context_id)
    messenger = notify.messenger_for(q.provider)
    if ctx is None or ctx.kind != "dm" or ctx.provider != q.provider or messenger is None:
        return False
    try:
        ok = messenger.send_to_context(ctx, text)
    except DeliveryFailed as exc:
        audit(session, "reception", "question_relay_failed", question_id=q.id, reason=exc.kind)
        return False
    except Exception as exc:
        audit(session, "reception", "question_relay_failed", question_id=q.id, reason=type(exc).__name__)
        return False
    if ok is False:
        audit(session, "reception", "question_relay_failed", question_id=q.id, reason="refused")
        return False
    log_event(
        session, context=ctx, direction="out", content=text, audience=Audience(has_unknown=True),
        meta={"visitor_question": kind, "question_id": str(q.id)},
    )  # fmt: skip
    return True


def answer(session: Session, q: VisitorQuestion, text: str) -> tuple[bool, str]:
    """The owner's typed answer, relayed once inside the fixed template. Returns (taken, notice): `taken` is False
    when nothing was done (the notice says why); True with a notice when the answer was kept but could not be
    sent. Either way a question is answered at most once, so it is never sent twice."""
    from superaide.policy.killswitch import is_frozen

    expire_old(session)
    if q.state != "asked":
        return False, f"This question is already {q.state}."
    body = plain(text, ANSWER_CHARS)
    if not body:
        return False, "Type an answer first."
    if is_frozen(session):
        return False, "Ava is paused, so nothing can be sent. Resume her in the app first."
    q.answer, q.state, q.answered_at = body, "answered", datetime.now(UTC)
    sent = _send(session, q, TEMPLATE.format(question=q.question, answer=body), kind="answer")
    q.delivery = "sent" if sent else "failed"
    audit(session, "reception", "question_answered", question_id=q.id, delivered=sent)
    return True, "" if sent else "Saved, but I could not send it: that channel is not connected or refused it."


def decline(session: Session, q: VisitorQuestion) -> str:
    expire_old(session)
    if q.state != "asked":
        return f"This question is already {q.state}."
    q.state, q.answered_at = "declined", datetime.now(UTC)
    sent = _send(session, q, DECLINED, kind="declined")
    q.delivery = "sent" if sent else "failed"
    audit(session, "reception", "question_declined", question_id=q.id, delivered=sent)
    return ""
