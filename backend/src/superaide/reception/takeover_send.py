"""Sending the owner's own words to a visitor and taking the conversation over (#493). Kept apart from the state
module (`reception/takeover.py`) so the pipeline, which only asks whether a takeover is active, does not import the
channel notification code."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.identity.models import Person
from superaide.memory.audience import Audience
from superaide.memory.models import Context, Event
from superaide.platform.audit import audit
from superaide.reception import ask
from superaide.reception.inbox import plain
from superaide.reception.takeover import MAX_TEXT, _row, start_or_refresh

LABEL = "Message from {who} (not Ava): {text}"
JOINED = "{who} has joined this chat (not Ava). Ava is not replying now. Message from {who}: {text}"
PER_HOUR = 30  # messages the owner can send into one conversation in an hour
UNDELIVERED = "Saved, but that channel is not connected or refused it. Ava is still stepped out."


class RateLimited(Exception):
    pass


def write(session: Session, ctx: Context, owner: Person, text: str) -> tuple[bool, bool]:
    """The owner's own words to the visitor. Returns (taken_over, delivered). The takeover starts whatever the platform
    said (the owner stepped in, so Ava must not answer on top of them); `delivered` says whether it reached them."""
    from superaide.channels.health import DeliveryFailed
    from superaide.memory.store import log_event
    from superaide.policy.killswitch import is_frozen
    from superaide.proactive import notify

    body = plain(text, MAX_TEXT)
    if not body:
        return False, False
    if is_frozen(session):
        return False, False
    now = datetime.now(UTC)
    recent = session.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            Event.context_id == ctx.id,
            Event.direction == "out",
            Event.meta.has_key("owner_live"),
            Event.created_at >= now - timedelta(hours=1),
        )
    )
    if (recent or 0) >= PER_HOUR:
        audit(session, "takeover", "rate_limited", context_id=ctx.id)
        raise RateLimited(f"That's {PER_HOUR} messages in an hour to this conversation. Try again in a while.")
    who = ask.owner_ref(session, owner)
    joining = _row(session, ctx.id) is None  # the person is told once that a human has joined (never Ava pretending)
    label = (JOINED if joining else LABEL).format(who=who, text=body)
    delivered = False
    messenger = notify.messenger_for(ctx.provider)
    if messenger is not None:
        try:
            delivered = messenger.send_to_context(ctx, label) is not False
        except DeliveryFailed as exc:
            audit(session, "takeover", "send_failed", context_id=ctx.id, reason=exc.kind)
        except Exception as exc:
            audit(session, "takeover", "send_failed", context_id=ctx.id, reason=type(exc).__name__)
    log_event(
        session, context=ctx, direction="out", content=body, audience=Audience(has_unknown=True),
        meta={"owner_live": True, "delivered": delivered},
    )  # fmt: skip
    start_or_refresh(session, ctx, owner, now)
    from superaide.commitments.api import close_for_context

    close_for_context(session, owner, ctx.id, now)  # she wrote to them herself: the promises to them are kept (#480)
    return True, delivered
