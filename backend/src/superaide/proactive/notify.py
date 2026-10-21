"""Tell the owner something: always in the app, and on Telegram or Slack too when they chose it.

A reminder can name its own channel; anything else follows the owner's interruption
preference ("default"). Messages only ever go to the owner's own linked accounts."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app.accounts import web_message
from superaide.audit import audit
from superaide.channels import health
from superaide.context.audience import Audience
from superaide.db.models import ChannelIdentity, Person
from superaide.knowledge.store import preferences_for
from superaide.memory.store import get_or_create_context, log_event
from superaide.policy.killswitch import get_state, set_state

log = logging.getLogger(__name__)


EXTERNAL = ("telegram", "slack")  # channels a message can be pushed to besides the app
CHANNELS = ("default", "app", *EXTERNAL)
CHANNEL_NAMES = {"app": "the app", "telegram": "Telegram", "slack": "Slack"}
NOTE_EVERY = timedelta(hours=3)  # at most one "couldn't send this on X" note per channel in this window


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
    """The app, plus each channel the owner linked an account on (whether or not it works right now)."""
    return ["app", *(p for p in EXTERNAL if identity_for(session, owner, p) is not None)]


def rejected_channels(session: Session) -> list[str]:
    """Channels whose token the service rejected: nothing can be sent there until reconnected."""
    return [p for p in EXTERNAL if health.rejected(session, p)]


def available_channels(session: Session, owner: Person) -> list[str]:
    """Where a message to the owner can actually go right now."""
    bad = set(rejected_channels(session))
    return [c for c in linked_channels(session, owner) if c not in bad]


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
    """True when the message went out. Raises health.DeliveryFailed when the channel is down."""
    messenger = messenger_for(provider)
    if messenger is None:
        return False
    return messenger.send_dm(identity, text) is not False  # the fakes return None


def send_telegram(identity: ChannelIdentity, text: str) -> bool:
    return send_via("telegram", identity, text)


def _push(provider: str, identity: ChannelIdentity, text: str) -> tuple[bool, str]:
    """(sent, why not) for one push to Telegram or Slack."""
    try:
        sent = send_telegram(identity, text) if provider == "telegram" else send_via(provider, identity, text)
    except health.DeliveryFailed as exc:
        log.warning("%s notification failed: %s", provider, exc.kind)
        return False, health.describe(provider, {"status": exc.kind})
    except Exception:  # the in-app copy is already there
        log.exception("%s notification failed", provider)
        return False, f"{CHANNEL_NAMES[provider]} didn't accept the message."
    if sent:
        return True, ""
    if messenger_for(provider) is None:
        return False, f"{CHANNEL_NAMES[provider]} isn't connected any more. Connect it under Connections."
    return False, f"{CHANNEL_NAMES[provider]} didn't accept the message."


def _note_failure(session: Session, owner: Person, ctx: Any, provider: str, why: str, meta: dict[str, Any]) -> None:
    """Tell the owner in the app that the copy on `provider` didn't go out: once per channel per few hours."""
    audit(session, "notify", "delivery_failed", provider=provider, reason=why, kind=meta.get("kind"))
    state_key = f"channel_failure_noted:{provider}"
    last = get_state(session, state_key).get("at")
    now = datetime.now(UTC)
    if last and now - datetime.fromisoformat(last) < NOTE_EVERY:
        return
    set_state(session, state_key, {"at": now.isoformat()})
    note = f"I couldn't send this on {CHANNEL_NAMES[provider]}: {why}"
    log_event(
        session, context=ctx, direction="out", content=note, audience=Audience(person_ids={owner.id}),
        meta={"kind": "delivery_failed", "channel": provider},
    )  # fmt: skip
    audit(session, "notify", "delivery_failure_noted", provider=provider)


def notify_owner(
    session: Session, owner: Person, text: str, meta: dict[str, Any] | None = None, *, channel: str = "default"
) -> list[str]:
    """Returns where it went, e.g. ["app"] or ["app", "telegram"]. `channel` "app" keeps it in
    the app; "telegram"/"slack" also sends it there when linked; "default" follows the owner's
    interruption preference. When the other channel can't take it, the owner gets a note in the app
    saying so (rate-limited), so a failed delivery is never silent."""
    ctx = get_or_create_context(session, web_message(owner, ""))
    log_event(
        session, context=ctx, direction="out", content=text, audience=Audience(person_ids={owner.id}), meta=meta or {}
    )
    via = ["app"]
    if channel == "default":
        channel = "telegram" if preferences_for(session, owner).interrupt_channel == "telegram" else "app"
    if channel in EXTERNAL:
        identity = identity_for(session, owner, channel)
        if identity is None:
            why = f"your {CHANNEL_NAMES[channel]} account isn't linked any more. Link it under Connections."
            _note_failure(session, owner, ctx, channel, why, meta or {})
            return via
        # Try even when the channel is marked unhealthy: a send that works is what heals it.
        sent, why = _push(channel, identity, text)
        if sent:
            via.append(channel)
        else:
            _note_failure(session, owner, ctx, channel, why, meta or {})
    return via
