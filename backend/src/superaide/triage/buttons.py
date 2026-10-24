"""Buttons on escalation pushes (ADR 0010 TR4, section 6): Acknowledge, Snooze 30 min, Not urgent.

- **Out:** each Telegram push carries an inline keyboard whose `callback_data` is
  `e1:<token>:<verb>`. The token is the delivery's own random 12 characters, never the
  escalation's id, so a button reveals nothing and can't be guessed.
- **In:** a press is a `callback_query`. Only the instance owner's own linked Telegram account
  may press, and only on the bot it was confirmed on. Anyone else (a forwarded message in a
  group) is refused and audited. The press's id makes a repeated press a no-op (`escalation_ack`
  is unique per source and id).
- **After:** the press is always answered (`answerCallbackQuery`, so the client stops spinning).
  The message is edited to say what happened and drop its buttons, and every other copy of the
  escalation on Telegram loses its buttons too.

Slack buttons (`block_actions`) are not built yet; Slack pushes stay plain text.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.channels.base import MESSENGER_FACTORIES, PRESS_HANDLERS, Button, InboundAction
from superaide.identity.trust import Trust
from superaide.jobs.queue import enqueue, handler
from superaide.platform.audit import audit
from superaide.platform.db.models import ChannelIdentity, Job, Person
from superaide.platform.db.session import session_scope
from superaide.proactive import notify
from superaide.triage.models import Escalation, EscalationDelivery

log = logging.getLogger(__name__)

VERBS = {"a": "ack", "s": "snooze_30m", "n": "not_urgent"}
LABELS = {"a": "Acknowledge", "s": "Snooze 30 min", "n": "Not urgent"}
DONE = {"ack": "Acknowledged", "snooze_30m": "Snoozed for 30 minutes", "not_urgent": "Marked not urgent"}
DATA = re.compile(r"^e1:([A-Za-z0-9_-]{8,24}):([asn])$")
JOB_REFRESH = "escalation_refresh"


def keyboard(token: str) -> list[Button]:
    return [(LABELS[v], f"e1:{token}:{v}") for v in ("a", "s", "n")]


def _stamp(text: str, verb: str, via: str, at: datetime) -> str:
    return f"{text}\n\n{DONE.get(verb, 'Handled')} via {via} at {at:%H:%M} UTC."


def _owner_identity(session: Session, esc: Escalation, action: InboundAction) -> Person | None:
    """The owner, if the presser's verified Telegram account is theirs and confirmed on this bot."""
    from superaide.identity.resolver import endpoint_confirmed

    ident = session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.provider == "telegram",
            ChannelIdentity.stable_key == action.sender_id,
            ChannelIdentity.lifecycle == "active",
            ChannelIdentity.person_id == esc.owner_person_id,
        )
    )
    if ident is None or ident.trust_level < int(Trust.LINKED) or not endpoint_confirmed(ident, action.endpoint):
        return None
    return session.get(Person, esc.owner_person_id)


def on_telegram_press(bot_token: str, action: InboundAction) -> str:
    """Handle one press; returns what the presser is told. Never raises: the press is always answered."""
    reply = "That button has expired."
    try:
        reply = press(action)
    except Exception:
        log.exception("escalation button press failed")
        reply = "Something went wrong. Open Needs you in the app."
    # The instance's own bot (the poller and webhook run in its scope); the receiving bot otherwise.
    messenger: Any = notify.messenger_for("telegram") or MESSENGER_FACTORIES["telegram"](bot_token)
    if hasattr(messenger, "answer_callback"):
        messenger.answer_callback(action.action_id, reply)
    return reply


def press(action: InboundAction) -> str:
    from superaide.triage import escalate

    if not action.verified:
        return "That button has expired."
    m = DATA.match(action.data)
    if m is None:
        return "That button has expired."
    token, code = m.groups()
    verb = VERBS[code]
    with session_scope() as s:
        delivery = s.scalar(select(EscalationDelivery).where(EscalationDelivery.token == token))
        esc = s.get(Escalation, delivery.escalation_id) if delivery is not None else None
        if delivery is None or esc is None:
            return "That button has expired."
        owner = _owner_identity(s, esc, action)
        if owner is None:
            audit(s, "triage", "escalation_ack_refused", escalation_id=esc.id, source="telegram", verb=verb)
            return "Only the person this was sent to can do that."
        changed = escalate.ack(s, esc.id, source="telegram", external_id=action.action_id, verb=verb, actor=owner)
        return DONE[verb] if changed else "Already handled."


def refresh(session: Session, esc: Escalation, *, verb: str, via: str) -> None:
    """Queue the edit that stamps every Telegram copy of this escalation and removes its buttons
    (called by escalate.ack, whichever way the owner answered)."""
    has_copies = session.scalar(
        select(EscalationDelivery.id)
        .where(
            EscalationDelivery.escalation_id == esc.id,
            EscalationDelivery.provider == "telegram",
            EscalationDelivery.external_ref != "",
        )
        .limit(1)
    )
    if has_copies is None:
        return
    at = datetime.now(UTC)
    enqueue(
        session, JOB_REFRESH, {"escalation_id": str(esc.id), "verb": verb, "via": via, "at": at.isoformat()},
        dedupe_key=f"escalation-refresh:{esc.id}:{at.isoformat()}",
    )  # fmt: skip


@handler(JOB_REFRESH)
def _refresh_job(session: Session, job: Job) -> None:
    from superaide.triage.escalate import text_for
    from superaide.triage.models import TriageDecision

    esc = session.get(Escalation, job.payload["escalation_id"])
    if esc is None:
        return
    decision = session.get(TriageDecision, esc.decision_id)
    messenger = notify.messenger_for("telegram")
    if decision is None or messenger is None or not hasattr(messenger, "edit_message"):
        return
    at = datetime.fromisoformat(job.payload["at"])
    rows = session.scalars(
        select(EscalationDelivery).where(
            EscalationDelivery.escalation_id == esc.id,
            EscalationDelivery.provider == "telegram",
            EscalationDelivery.status == "sent",
            EscalationDelivery.external_ref != "",
        )
    ).all()
    text = text_for(session, esc, decision, push=True)
    for row in rows:
        messenger.edit_message(row.external_ref, _stamp(text, job.payload["verb"], job.payload["via"], at))


PRESS_HANDLERS["telegram"] = on_telegram_press
