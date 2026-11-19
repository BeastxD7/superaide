"""Sending the owner's own words to a visitor and taking the conversation over (#493). Kept apart from the state
module (`reception/takeover.py`) so the pipeline, which only asks whether a takeover is active, does not import the
channel notification code."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from superaide.identity.models import Person
from superaide.memory.audience import Audience
from superaide.memory.models import Context
from superaide.platform.audit import audit
from superaide.reception import ask
from superaide.reception.inbox import plain
from superaide.reception.takeover import MAX_TEXT, start_or_refresh

LABEL = "Message from {who} (not Ava): {text}"
UNDELIVERED = "Saved, but that channel is not connected or refused it. Ava is still stepped out."


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
    label = LABEL.format(who=ask.owner_ref(session, owner), text=body)
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
    return True, delivered
