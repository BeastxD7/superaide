"""Proxy messaging (#478): "tell Meera I'm busy, I'll reply in an hour", typed by the owner in the app.

Ava speaks to someone she doesn't know only on the owner's instruction, and only after the owner confirmed the exact
words and the exact recipient on an approval card (kind `relay_message`). Nothing here lets the model decide who or
what is sent:

- The recipient is a stored conversation (`reception_message` -> channel identity -> DM context), found by code from
  the name the owner typed. The model's `to` must appear in the owner's own words; no match says so, two matches is a
  question back to the owner, never a guess. The platform id is what the executor sends to, not a name.
- The model drafts only the text. Code wraps it in a fixed introduction (who Ava is), cuts and cleans it, refuses a
  link or address the owner did not type, and flags a time, number or day the owner did not say (`adds`) so the
  card warns about it.
- The turn must be clean: after the owner read a stranger's words (inbox), a channel, mail or a file, no relay
  may be requested in it (the stranger could be the one asking).
- Approving is the second step. The executor re-checks everything, honours the kill switch, caps relays per
  recipient per day, sends, writes the message into the conversation and audits it.

Not here yet (see the PR): a signed mandate that skips the card, a commitment for "I'll reply in an hour", a
dedicated `relay` table (the approval row is the record) and replies routed as threads."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.approvals.models import Approval
from superaide.identity.models import ChannelIdentity, Person
from superaide.memory.audience import Audience
from superaide.memory.models import Context
from superaide.platform.audit import audit
from superaide.policy.registry import EXECUTORS
from superaide.reception.inbox import CHANNEL, plain
from superaide.reception.models import ReceptionMessage

KIND = "relay_message"
WINDOW = timedelta(days=30)
MAX_TEXT = 400
PER_RECIPIENT_PER_DAY = 3  # relays that actually went to one person in 24 hours: code's cap, whatever anyone asks

RELAY_TOOL: dict[str, Any] = {
    "name": "relay_message",
    "description": (
        "Send a short message to someone Ava doesn't know who wrote to her (see the inbox) when the owner asks: "
        "'tell Meera I'm busy', 'let Rahul know I'll reply in an hour'. Pass `to` (the name exactly as the owner "
        "said it) and `text` (what to tell them, in plain words, using ONLY what the owner said: add no time, "
        "number, link or promise they did not state). This never sends anything: it puts the exact text and the "
        "recipient on a card in the owner's app, and it is sent only after the owner approves it. Tell the owner it "
        "is waiting for their OK. If the tool says the name matches no one or several people, say that and ask."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "The recipient's name as the owner typed it"},
            "text": {"type": "string", "description": "What to tell them: only what the owner said, short"},
        },
        "required": ["to", "text"],
        "additionalProperties": False,
    },
}

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_LINK = re.compile(r"https?://|www\.|[\w.+-]+@[\w-]+\.[\w.]+", re.IGNORECASE)
_DAYS = (
    "today tonight tomorrow monday tuesday wednesday thursday friday saturday sunday noon midnight morning evening"
).split()


def channel_name(provider: str) -> str:
    return CHANNEL.get(provider, provider.capitalize())


def _first(word: str) -> str:
    return "".join(c for c in (word.split() or [""])[0] if c.isalpha() or c in "'-").strip("'-").lower()


def _row_first_name(row: ReceptionMessage) -> str:
    return _first(row.name)


def adds(text: str, typed: str) -> list[str]:
    """What the draft says that the owner's words do not: numbers and times, and day words. Shown on the card
    (the owner confirms every relay anyway); a model that invents "I'll call at 3" is visible, not silent."""
    said = typed.lower()
    found: list[str] = []
    for tok in re.findall(r"[\w:.]*\d[\w:.]*", text):
        if tok.lower() not in said and tok not in found:
            found.append(tok)
    low = text.lower()
    for day in _DAYS:
        if re.search(rf"\b{day}\b", low) and not re.search(rf"\b{day}\b", said) and day not in found:
            found.append(day)
    return found[:6]


def _candidates(session: Session, to: str) -> list[list[ReceptionMessage]]:
    """The conversations (one list of rows per sender, newest first) whose given first name is `to`'s first
    word, or whose short id is `to`. Only senders Ava still doesn't know: a linked contact is reached normally."""
    wanted = _first(to)
    since = datetime.now(UTC) - WINDOW
    rows = list(
        session.scalars(
            select(ReceptionMessage)
            .where(ReceptionMessage.created_at >= since)
            .order_by(ReceptionMessage.created_at.desc())
            .limit(500)
        )
    )
    groups: dict[uuid.UUID, list[ReceptionMessage]] = {}
    for r in rows:
        groups.setdefault(r.identity_id, []).append(r)
    found = []
    for group in groups.values():
        if wanted and any(_row_first_name(r) == wanted for r in group):
            found.append(group)
    return found


def _owner_words(typed: str, to: str) -> bool:
    """The recipient's name is in what the owner typed: the model alone never picks a person."""
    word = _first(to)
    return bool(word) and word in {w.lower().strip("'’-") for w in _WORD.findall(typed)}


def _sent_today(session: Session, identity_id: uuid.UUID) -> int:
    since = datetime.now(UTC) - timedelta(hours=24)
    rows = session.scalars(
        select(Approval.payload).where(
            Approval.kind == KIND, Approval.status == "approved", Approval.decided_at >= since
        )
    ).all()
    return sum(
        1 for p in rows if p.get("identity_id") == str(identity_id) and str(p.get("result", "")).startswith("Sent")
    )


def _introduction(session: Session, to_name: str) -> str:
    from superaide.app.accounts import owner as instance_owner
    from superaide.identity.vault.store import runtime_settings
    from superaide.reception import service
    from superaide.reception import turn as rturn

    agent = runtime_settings().agent_name
    owner = instance_owner(session)
    shown = ""
    if owner is not None and service.settings_for(session, owner).show_first_name:
        shown = rturn.first_name(owner)
    who = f"{shown}'s AI assistant" if shown else "an AI assistant"
    hi = f"Hi {to_name}," if to_name else "Hi,"
    return f"{hi} this is {agent}, {who}."


def request(session: Session, owner: Person, sender: Any, *, to: str, text: str, typed: str) -> tuple[str, bool]:
    """The tool's work: (what to tell the model, is it an error). Opens one approval card or refuses, in code."""
    from superaide.policy.actions import TOOL_RULES, decide

    to = plain(to, 60)
    body = plain(text, MAX_TEXT)
    if not body:
        return "No message was drafted: ask the owner what to tell them.", True
    if not _owner_words(typed, to):
        audit(session, "reception", "relay_refused", reason="name not typed by owner")
        return (
            "Refused: the name must be one the owner typed in their own message. Ask the owner who to tell.",
            True,
        )
    found = _candidates(session, to)
    if not found:
        audit(session, "reception", "relay_refused", reason="no match")
        return (
            f"No one called {to} has written to Ava (people she doesn't know, in the last 30 days), so nothing "
            "was queued. Tell the owner, and offer to check the inbox for who has written.",
            True,
        )
    if len(found) > 1:
        audit(session, "reception", "relay_refused", reason="ambiguous", candidates=len(found))
        options = "; ".join(
            f"{channel_name(g[0].provider)}, last wrote {g[0].created_at:%a %H:%M} UTC" for g in found[:5]
        )
        return (
            f"{len(found)} people called {to} have written ({options}). NOTHING was queued. Ask the owner which "
            "one (the channel or when they wrote), and call this tool again with that.",
            True,
        )
    group = found[0]
    latest = group[0]
    identity = session.get(ChannelIdentity, latest.identity_id)
    ctx = session.get(Context, latest.context_id)
    if identity is None or ctx is None or ctx.kind != "dm" or identity.lifecycle != "active":
        return "That conversation can't be answered from here. Tell the owner.", True
    if identity.person_id is not None:
        return f"{to} is someone Ava knows now: message them in your usual way instead. Nothing was queued.", True
    if _LINK.search(body) and not _LINK.search(typed):
        audit(session, "reception", "relay_refused", reason="link not typed by owner")
        return "Refused: the owner did not give a link or an address, so none may go in the message.", True
    pending = session.scalar(
        select(Approval.id)
        .where(
            Approval.kind == KIND,
            Approval.status == "pending",
            Approval.payload["identity_id"].as_string() == str(identity.id),
        )
        .limit(1)
    )
    if pending is not None:
        return (
            f"A message to {to} is already waiting for the owner's OK. Nothing new was queued: tell them to decide "
            "that one first.",
            True,
        )
    if _sent_today(session, identity.id) >= PER_RECIPIENT_PER_DAY:
        audit(session, "reception", "relay_refused", reason="recipient cap")
        return (
            f"{PER_RECIPIENT_PER_DAY} messages already went to {to} in the last day, so nothing was queued. "
            "Tell the owner they can try again tomorrow.",
            True,
        )
    name = _row_first_name(latest).capitalize() or ""
    final = f"{_introduction(session, name)} {body}"
    extra = adds(body, typed)
    where = channel_name(latest.provider)
    shown = name or "someone who gave no name"
    summary = f"Send {shown} on {where}: “{final}”"
    decision = decide(
        session,
        kind=KIND,
        summary=summary,
        requester=sender,
        rules=TOOL_RULES,
        payload={
            "identity_id": str(identity.id),
            "context_id": str(ctx.id),
            "provider": latest.provider,
            "recipient": shown,
            "text": final,
            "instruction": plain(typed.splitlines()[0] if typed else "", 300),
            "adds": extra,
        },
    )
    if decision.outcome != "approval":
        return f"Refused: {decision.reason}.", True
    audit(session, "reception", "relay_requested", approval_id=decision.approval.id if decision.approval else None)
    warn = (
        f" It adds {', '.join(extra)}, which the owner did not say: tell them to check it before they approve."
        if extra
        else ""
    )
    return (
        f"Queued for the owner's approval: the card shows the exact message and the recipient ({shown} on {where}). "
        f"Nothing is sent until they approve.{warn} Tell them it is waiting.",
        False,
    )


def _local(session: Session, at: datetime) -> str:
    """The time in the owner's zone, as 14:08."""
    from superaide.app.accounts import owner as instance_owner
    from superaide.intentions.when import zone
    from superaide.memory.knowledge.store import preferences_for

    owner = instance_owner(session)
    if owner is None:
        return f"{at:%H:%M} UTC"
    return f"{at.astimezone(zone(preferences_for(session, owner).timezone)):%H:%M}"


def execute(session: Session, approval: Approval) -> str:
    """The owner approved this exact text to this exact conversation: send it, record it, say what happened."""
    from superaide.channels.health import DeliveryFailed
    from superaide.memory.store import log_event
    from superaide.policy.killswitch import is_frozen
    from superaide.proactive import notify

    data = approval.payload or {}
    if is_frozen(session):
        return "Not sent: Ava is paused. Resume her in the app and ask again."
    try:
        identity = session.get(ChannelIdentity, uuid.UUID(str(data.get("identity_id"))))
        ctx = session.get(Context, uuid.UUID(str(data.get("context_id"))))
    except ValueError:
        return "Not sent: that conversation is gone."
    text = str(data.get("text") or "")
    if identity is None or ctx is None or not text or ctx.kind != "dm" or identity.lifecycle != "active":
        return "Not sent: that conversation is gone."
    # The conversation must really be this person's: the context they wrote in, on the same platform.
    wrote_here = session.scalar(
        select(ReceptionMessage.id)
        .where(ReceptionMessage.identity_id == identity.id, ReceptionMessage.context_id == ctx.id)
        .limit(1)
    )
    if wrote_here is None or ctx.provider != identity.provider or ctx.provider != data.get("provider"):
        audit(session, "reception", "relay_refused", reason="recipient mismatch", approval_id=approval.id)
        return "Not sent: the recipient no longer matches the conversation."
    if _sent_today(session, identity.id) >= PER_RECIPIENT_PER_DAY:
        return f"Not sent: {PER_RECIPIENT_PER_DAY} messages already went to them today."
    where = channel_name(ctx.provider)
    messenger = notify.messenger_for(ctx.provider)
    if messenger is None:
        return f"Not sent: {where} is not connected. Connect it under Connections and ask again."
    try:
        ok = messenger.send_to_context(ctx, text)
    except DeliveryFailed as exc:
        audit(session, "reception", "relay_failed", approval_id=approval.id, reason=exc.kind)
        return f"Not sent: {where} could not deliver it ({exc.kind})."
    except Exception as exc:
        audit(session, "reception", "relay_failed", approval_id=approval.id, reason=type(exc).__name__)
        return f"Not sent: {where} refused it."
    if ok is False:
        audit(session, "reception", "relay_failed", approval_id=approval.id, reason="refused")
        return f"Not sent: {where} refused it."
    now = datetime.now(UTC)
    log_event(
        session, context=ctx, direction="out", content=text, audience=Audience(has_unknown=True),
        meta={"relay": True, "approval_id": str(approval.id)},
    )  # fmt: skip
    audit(session, "reception", "relay_sent", approval_id=approval.id, provider=ctx.provider)
    return f"Sent to {data.get('recipient') or 'them'} on {where} at {_local(session, now)}."


EXECUTORS[KIND] = execute
