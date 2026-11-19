"""Approve and Cancel buttons under an approval card's notice, on Telegram and Slack (#642).

- **Out:** after the owner's reply that announced a card, code posts one short message with two buttons. The
  button data is `ap1:<approval id>:y|n`: an id, never text, and it decides nothing by itself.
- **In:** a press is a platform-signed `InboundAction`. It is accepted only when the presser's verified account is
  the owner's own (by platform id, confirmed on this bot or app), the press came from the very direct message the
  card was asked for in, and then it goes through `shortcut.decide_in_chat`: the same checks as the typed word
  (kind on the list, clean turn, not from a call, seal, trust, brake) and the same `resolve`. A second press, or a
  press after the card was decided anywhere else, is "already handled" and changes nothing.
- **After:** the pressed message is edited to say what happened, which drops its buttons.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.approvals.models import Approval
from superaide.approvals.shortcut import decide_in_chat, why_not_by_text
from superaide.channels.base import MESSENGER_FACTORIES, PRESS_HANDLERS, Button, InboundAction
from superaide.identity.models import ChannelIdentity
from superaide.identity.resolver import endpoint_confirmed, find_identity
from superaide.identity.trust import Trust
from superaide.memory.audience import Audience
from superaide.memory.models import Context
from superaide.memory.store import log_event
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope

log = logging.getLogger(__name__)

PREFIX = "ap1"
DATA = re.compile(r"^ap1:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([yn])$")
EXPIRED = "That button has expired."


def providers() -> set[str]:
    return {p.strip() for p in get_settings().approval_buttons.split(",") if p.strip()}


def keyboard(approval_id: Any) -> list[Button]:
    return [("Approve", f"{PREFIX}:{approval_id}:y"), ("Cancel", f"{PREFIX}:{approval_id}:n")]


def offer(session: Session, messenger: Any, ctx: Context, trust: int, approval_ids: list[str]) -> None:
    """Post the buttons for the cards a reply just announced. Cosmetic: the typed word and the app still work, so a
    failure here is logged and never changes the turn."""
    if ctx.kind != "dm" or ctx.provider not in providers() or not hasattr(messenger, "send_buttons"):
        return
    for raw in approval_ids[:3]:
        try:
            approval = session.get(Approval, uuid.UUID(raw))
            if approval is None or approval.status != "pending" or why_not_by_text(approval, trust):
                continue  # only a card this chat may approve gets a button that approves
            summary = str((approval.payload or {}).get("summary") or "")[:300]
            messenger.send_buttons(ctx.external_id, f"Waiting for your OK: {summary}", keyboard(approval.id))
        except Exception:
            log.warning("couldn't post approval buttons on %s", ctx.provider, exc_info=True)


def press(action: InboundAction) -> str:
    """What the presser is told. Never approves unless every check in `shortcut.decide_in_chat` holds."""
    if not action.verified:
        return EXPIRED
    m = DATA.match(action.data)
    if m is None:
        return EXPIRED
    approve = m.group(2) == "y"
    with session_scope() as s:
        approval = s.scalar(select(Approval).where(Approval.id == uuid.UUID(m.group(1))).with_for_update())
        if approval is None:
            return EXPIRED
        who = _presser(s, approval, action)
        if who is None:
            audit(s, "policy", "approval_press_refused", approval_id=approval.id, provider=action.provider)
            return "Only the person this was sent to can do that."
        person, identity, ctx = who
        if approval.status != "pending":
            return "Already handled."
        reply = decide_in_chat(
            s, approval, approve=approve, person=person, trust=identity.trust_level, provider=action.provider,
            how="button",
        )  # fmt: skip
        log_event(
            s, context=ctx, direction="out", content=reply.text, audience=Audience(person_ids={person.id}),
            meta={"kind": "approval_press", "approval_id": str(approval.id)},
        )  # fmt: skip
        return reply.text


def _presser(
    session: Session, approval: Approval, action: InboundAction
) -> tuple[Any, ChannelIdentity, Context] | None:
    """(owner, their identity, the card's conversation) if this press is the owner's, from where the card was asked."""
    from superaide.app.accounts import owner as instance_owner
    from superaide.identity.models import Person

    p = approval.payload or {}
    try:
        ctx = session.get(Context, uuid.UUID(str(p.get("origin_context_id"))))
    except ValueError:
        return None
    if ctx is None or ctx.kind != "dm" or ctx.audience_class != "private_1to1" or ctx.provider != action.provider:
        return None
    if ctx.external_id != action.chat_id:  # a forwarded copy, or another chat: only the original DM decides
        return None
    ident = find_identity(session, action.provider, action.tenant_scope, action.sender_id)
    boss = instance_owner(session)
    if (
        ident is None
        or boss is None
        or ident.person_id != boss.id
        or approval.requested_by_person_id != boss.id
        or ident.lifecycle != "active"
        or ident.trust_level < int(Trust.LINKED)
        or "external" in (ident.risk_flags or [])
        or (ident.verification or {}).get("pending")  # linked but not confirmed on this bot or app (identity/resolver)
        or not endpoint_confirmed(ident, action.endpoint)
    ):
        return None
    person = session.get(Person, boss.id)
    return (person, ident, ctx) if person is not None and person.status == "active" else None


def on_press(bot_token: str, action: InboundAction) -> str:
    """The Telegram or Slack listener's entry: decide, then answer the press and edit the card message. Never raises."""
    try:
        reply = press(action)
    except Exception:
        log.exception("approval button press failed")
        reply = "Something went wrong. Open Approvals in the app."
    try:
        from superaide.proactive import notify

        messenger: Any = notify.messenger_for(action.provider) or MESSENGER_FACTORIES[action.provider](bot_token)
        if hasattr(messenger, "answer_callback"):
            messenger.answer_callback(action.action_id, reply)
        if action.chat_id and action.message_id and hasattr(messenger, "edit_message"):
            messenger.edit_message(f"{action.chat_id}:{action.message_id}", reply)  # drops the buttons
    except Exception:
        log.warning("couldn't update the approval message", exc_info=True)
    return reply


PRESS_HANDLERS.setdefault("slack", on_press)
