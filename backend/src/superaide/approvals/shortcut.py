"""Say yes or no where you are (#642): "send" / "cancel" in the owner's private chat decides the card Ava just
showed, without a model.

Everything here is decided in code, before any model runs. A word approves only when all of these hold:

- the sender is the confirmed owner (by platform id), in a direct message whose audience is the owner alone;
- the message is only the command, from a closed word list: short, no attachment, no quote, no extra words;
- exactly one card of this conversation is waiting, and Ava's latest message here is the one that announced it
  (several waiting: Ava lists them with numbers and "send 2" answers that list; nothing is ever guessed);
- the card's kind is on the owner's list (`APPROVE_BY_REPLY_KINDS`), it was asked for in a turn that read nobody
  else's words, did not begin on a call, still matches its seal, and this chat identity is trusted enough for it;
- it has not expired (an expired card is closed with that said, never run).

A "yes" that is not allowed here changes nothing and says to use the app. A "no" needs only the same conversation
and the same latest-notice rule: refusing is always safe. The decision goes through `policy.actions.resolve`, so the
burst brake, kill switch, content binding and the executor's own checks all run exactly as for a tap in the app,
and the reply is written from the executor's result, never by the model. A button press (`approvals/buttons.py`)
ends in the same `decide_in_chat`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.approvals.binding import EXPIRED_BY, check, expired
from superaide.approvals.models import Approval
from superaide.identity.trust import Trust
from superaide.memory.models import Context, Event
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.policy import kinds

log = logging.getLogger(__name__)

APPROVE = frozenset(
    {
        "send", "send it", "send it now", "send now", "send the message", "send the message now", "please send",
        "please send it", "yes", "yes please", "yes send", "yes send it", "yes please send it", "yes send it now",
        "yes please send the message now", "yes send the message now", "ok", "okay", "ok send", "ok send it",
        "okay send it", "sure", "yep", "yeah", "go ahead", "do it", "approve", "approve it", "i approve", "confirm",
    }
)  # fmt: skip
REJECT = frozenset(
    {
        "cancel", "cancel it", "no", "no thanks", "no thank you", "don't send", "dont send", "don't send it",
        "do not send", "do not send it", "never mind", "nevermind", "discard", "discard it", "reject", "reject it",
    }
)  # fmt: skip
_NUMBERED = re.compile(r"(send|approve|yes|ok|okay|cancel|no|reject|discard) (?:number |no\. ?|#)?([1-9])")
MAX_CHARS = 40


@dataclass
class Command:
    approve: bool
    number: int | None = None


@dataclass
class Reply:
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def allowed_kinds() -> set[str]:
    return {k.strip() for k in get_settings().approve_by_reply_kinds.split(",") if k.strip()}


def parse(text: str) -> Command | None:
    """The whole message is one of the closed words, nothing else. Anything pasted, quoted or longer is not."""
    t = (text or "").strip().lower().replace("’", "'")
    if not t or len(t) > MAX_CHARS or "\n" in t:
        return None
    t = re.sub(r"[.!]+$", "", t).strip()
    t = re.sub(r" {2,}", " ", t)
    if t in APPROVE:
        return Command(True)
    if t in REJECT:
        return Command(False)
    m = _NUMBERED.fullmatch(t)
    if m:
        return Command(m.group(1) in {"send", "approve", "yes", "ok", "okay"}, int(m.group(2)))
    return None


def why_not_by_text(approval: Approval, trust: int) -> str:
    """Why this card can only be approved in the app ("" = it may be approved here)."""
    p = approval.payload or {}
    if approval.kind not in allowed_kinds():
        return "this kind of request is approved in the app"
    if p.get("tainted"):
        return "it was asked for in a turn that read other people's words"
    if p.get("origin") == "voice":
        return "it was asked for on a call"
    if get_settings().approval_binding != "off" and check(approval) != "ok":
        return "it no longer matches what was shown"
    if trust < max(int(p.get("required_trust") or 0), int(Trust.LINKED)):
        return "this chat is not trusted enough for it"
    return ""


def _label(a: Approval) -> str:
    p = a.payload or {}
    base = kinds.info(a.kind).label
    if a.kind == "relay_message" and p.get("recipient"):
        return f"{base}: {str(p['recipient'])[:40]} on {str(p.get('provider') or 'chat').capitalize()}"
    return base


def waiting_here(session: Session, ctx: Context, person_id: Any) -> list[Approval]:
    return list(
        session.scalars(
            select(Approval)
            .where(
                Approval.status == "pending",
                Approval.requested_by_person_id == person_id,
                Approval.payload["origin_context_id"].as_string() == str(ctx.id),
            )
            .order_by(Approval.created_at, Approval.id)
            .with_for_update()  # a text and a button racing for the same card: one waits, then sees it decided
        )
    )


def latest_notice(session: Session, ctx: Context) -> Event | None:
    """Ava's latest message in this conversation (room messages are not conversation)."""
    return session.scalar(
        select(Event)
        .where(Event.context_id == ctx.id, Event.direction == "out", ~Event.meta.has_key("ambient"))
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(1)
    )


def choices(cards: list[Approval]) -> Reply:
    lines = [f"{i}. {_label(a)}" for i, a in enumerate(cards, 1)]
    return Reply(
        "More than one request is waiting for you here:\n" + "\n".join(lines)
        + '\nWhich one? Reply "send 1" or "cancel 2".',
        {"approval_choice": [str(a.id) for a in cards]},
        ["approval_choice"],
    )  # fmt: skip


def handle(session: Session, *, sender: Any, ctx: Context, aud: Any, msg: Any, is_owner: bool) -> Reply | None:
    """The reply to send for a one-word yes or no, or None when this message is not one (it then goes to the model)."""
    if not allowed_kinds():
        return None
    if not (ctx.kind == "dm" and sender.known and not sender.unconfirmed and sender.person is not None and is_owner):
        return None
    if ctx.audience_class != "private_1to1" or aud.has_unknown or aud.person_ids != {sender.person.id}:
        return None
    if msg.attachments or msg.file_refs or msg.uploads:
        return None
    cmd = parse(msg.text)
    if cmd is None:
        return None
    last = latest_notice(session, ctx)
    if last is None:
        return None
    meta = last.meta or {}
    cards = waiting_here(session, ctx, sender.person.id)
    if not cards:
        return None
    announced = {str(i) for i in meta.get("approvals") or []} | {str(i) for i in meta.get("approval_choice") or []}
    if not announced & {str(a.id) for a in cards}:
        return None  # Ava's latest message is not about a card: this "yes" is an answer to something else
    if cmd.number is not None:
        listed = [str(i) for i in meta.get("approval_choice") or []]
        if not listed or cmd.number > len(listed):
            return None
        chosen = next((a for a in cards if str(a.id) == listed[cmd.number - 1]), None)
        if chosen is None:
            return choices(cards)  # the list is out of date: show the current one
    elif len(cards) == 1:
        chosen = cards[0]
    else:
        return choices(cards)
    return decide_in_chat(
        session, chosen, approve=cmd.approve, person=sender.person, trust=sender.trust, provider=ctx.provider,
        how="text",
    )  # fmt: skip


def decide_in_chat(
    session: Session, approval: Approval, *, approve: bool, person: Any, trust: int, provider: str, how: str
) -> Reply:
    """Decide one card the owner answered in chat (`how`: "text" or "button"). Returns what to tell them."""
    from superaide.policy import brake
    from superaide.policy.actions import ApprovalError, resolve

    via = f"{provider}-{how}"[:16]
    if approve and not expired(approval):
        why = why_not_by_text(approval, trust)
        if why:
            audit(session, "policy", "approval_chat_refused", approval_id=approval.id, via=via, reason=why)
            return Reply(
                f"I can't approve that here: {why}. Open Approvals in the app to decide it.", notes=["app_only"]
            )
        if get_settings().rubber_stamp_brake_enabled and (
            held := brake.check(session, approval, approve=True, reason=None)
        ):
            audit(session, "policy", "approval_chat_refused", approval_id=approval.id, via=via, reason="brake")
            return Reply(f"{held} Open Approvals in the app to decide it.", notes=["app_only"])
    try:
        done = resolve(session, approval, approve=approve, decided_by=person.display_name, via=via, in_chat=provider)
    except ApprovalError as exc:
        return Reply(str(exc))
    audit(session, "policy", "approval_decided_in_chat", approval_id=approval.id, via=via, verdict=done.status)
    result = str((done.payload or {}).get("result") or "").strip()
    if done.status == "approved":
        text = result or "Approved."
    elif done.status == "pending":  # held by the burst brake: still the owner's to decide
        text = result or "Held: it is still waiting for you."
    elif done.decided_by == EXPIRED_BY:
        text = result or "Not run: this request expired. Ask Ava again."
    else:
        text = f"Cancelled. Nothing was done for: {_label(done)}."
    return Reply(text, notes=[f"approval:{done.status}"])
