"""Tell the owner something: always in the app, and on Telegram too when they chose it."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app.accounts import web_message
from superaide.context.audience import Audience
from superaide.db.models import ChannelIdentity, Person
from superaide.knowledge.store import preferences_for
from superaide.memory.store import get_or_create_context, log_event

log = logging.getLogger(__name__)


def telegram_identity(session: Session, owner: Person) -> ChannelIdentity | None:
    return session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.person_id == owner.id,
            ChannelIdentity.provider == "telegram",
            ChannelIdentity.lifecycle == "active",
        )
    )


def send_telegram(identity: ChannelIdentity, text: str) -> bool:
    from superaide.channels.telegram import TelegramMessenger
    from superaide.vault.store import runtime_settings

    token = runtime_settings().telegram_bot_token
    if not token:
        return False
    TelegramMessenger(token).send_dm(identity, text)
    return True


def notify_owner(session: Session, owner: Person, text: str, meta: dict[str, Any] | None = None) -> list[str]:
    """Returns where it went: ["app"] or ["app", "telegram"]."""
    ctx = get_or_create_context(session, web_message(owner, ""))
    log_event(
        session, context=ctx, direction="out", content=text, audience=Audience(person_ids={owner.id}), meta=meta or {}
    )
    via = ["app"]
    identity = telegram_identity(session, owner)
    if identity is not None and preferences_for(session, owner).interrupt_channel == "telegram":
        try:
            if send_telegram(identity, text):
                via.append("telegram")
        except Exception:  # the in-app copy is already there
            log.exception("telegram notification failed")
    return via
