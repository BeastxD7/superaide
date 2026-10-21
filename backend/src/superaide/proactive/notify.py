"""Tell the owner something: always in the app, and on Telegram or Slack too when they chose it.

A reminder can name its own channel; anything else follows the owner's interruption
preference ("default"). Messages only ever go to the owner's own linked accounts."""

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


EXTERNAL = ("telegram", "slack")  # channels a message can be pushed to besides the app
CHANNELS = ("default", "app", *EXTERNAL)
CHANNEL_NAMES = {"app": "the app", "telegram": "Telegram", "slack": "Slack"}


def identity_for(session: Session, owner: Person, provider: str) -> ChannelIdentity | None:
    return session.scalar(
        select(ChannelIdentity)
        .where(
            ChannelIdentity.person_id == owner.id,
            ChannelIdentity.provider == provider,
            ChannelIdentity.lifecycle == "active",
        )
        .limit(1)
    )


def telegram_identity(session: Session, owner: Person) -> ChannelIdentity | None:
    return identity_for(session, owner, "telegram")


def linked_channels(session: Session, owner: Person) -> list[str]:
    """Where a message to the owner can go: always the app, plus each linked account."""
    return ["app", *(p for p in EXTERNAL if identity_for(session, owner, p) is not None)]


def messenger_for(provider: str) -> Any:
    """A sender for Telegram or Slack with the bot token saved in the app, or None without one."""
    from superaide.vault.store import runtime_settings

    settings = runtime_settings()
    if provider == "telegram" and settings.telegram_bot_token:
        from superaide.channels.telegram import TelegramMessenger

        return TelegramMessenger(settings.telegram_bot_token)
    if provider == "slack" and settings.slack_bot_token:
        from superaide.channels.slack import SlackMessenger

        return SlackMessenger(settings.slack_bot_token)
    return None


def send_via(provider: str, identity: ChannelIdentity, text: str) -> bool:
    messenger = messenger_for(provider)
    if messenger is None:
        return False
    messenger.send_dm(identity, text)
    return True


def send_telegram(identity: ChannelIdentity, text: str) -> bool:
    return send_via("telegram", identity, text)


def _push(provider: str, identity: ChannelIdentity, text: str) -> bool:
    try:
        return send_telegram(identity, text) if provider == "telegram" else send_via(provider, identity, text)
    except Exception:  # the in-app copy is already there
        log.exception("%s notification failed", provider)
        return False


def notify_owner(
    session: Session, owner: Person, text: str, meta: dict[str, Any] | None = None, *, channel: str = "default"
) -> list[str]:
    """Returns where it went, e.g. ["app"] or ["app", "telegram"]. `channel` "app" keeps it in
    the app; "telegram"/"slack" also sends it there when linked; "default" follows the owner's
    interruption preference."""
    ctx = get_or_create_context(session, web_message(owner, ""))
    log_event(
        session, context=ctx, direction="out", content=text, audience=Audience(person_ids={owner.id}), meta=meta or {}
    )
    via = ["app"]
    if channel == "default":
        channel = "telegram" if preferences_for(session, owner).interrupt_channel == "telegram" else "app"
    if channel in EXTERNAL:
        identity = identity_for(session, owner, channel)
        if identity is not None and _push(channel, identity, text):
            via.append(channel)
    return via
