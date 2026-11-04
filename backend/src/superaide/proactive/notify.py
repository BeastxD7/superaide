"""Tell the owner something: always in the app, and on Telegram or Slack too when they chose it.

A reminder can name its own channel; anything else follows the owner's interruption
preference ("default"). Messages only ever go to the owner's own linked accounts."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app.accounts import web_message
from superaide.channels import health
from superaide.channels.base import MESSENGER_FACTORIES, bot_id
from superaide.identity.models import ChannelIdentity, Person
from superaide.jobs import queue as jobs
from superaide.jobs.models import Job
from superaide.memory.audience import Audience
from superaide.memory.knowledge.store import preferences_for
from superaide.memory.store import get_or_create_context, log_event
from superaide.platform.audit import audit
from superaide.policy.killswitch import get_state, set_state
from superaide.proactive.models import NotificationDelivery

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


def messenger_for(provider: str) -> Any:
    """A sender for Telegram or Slack with the bot token saved in the app, or None without one."""
    from superaide.identity.vault.store import runtime_settings

    settings = runtime_settings()
    if provider == "telegram" and settings.telegram_bot_token:
        return MESSENGER_FACTORIES["telegram"](settings.telegram_bot_token)
    if provider == "slack" and settings.slack_bot_token:
        return MESSENGER_FACTORIES["slack"](settings.slack_bot_token)
    return None


def confirmed_here(provider: str, identity: ChannelIdentity) -> bool:
    """Is this account confirmed on the bot connected now? (Links are bound to a bot; see identity/resolver.py.)"""
    if provider != "telegram":
        return True
    from superaide.identity.resolver import endpoint_confirmed
    from superaide.identity.vault.store import runtime_settings

    return endpoint_confirmed(identity, bot_id(runtime_settings().telegram_bot_token))


def send_via(provider: str, identity: ChannelIdentity, text: str) -> bool:
    """True when the message went out. Raises health.DeliveryFailed when the channel is down."""
    if not confirmed_here(provider, identity):
        # The bot changed and the owner hasn't confirmed this account on it: keep it in the app.
        log.info("not pushing to Telegram: account not confirmed on the current bot")
        return False
    messenger = messenger_for(provider)
    if messenger is None:
        return False
    return messenger.send_dm(identity, text) is not False  # the fakes return None


def send_telegram(identity: ChannelIdentity, text: str) -> bool:
    return send_via("telegram", identity, text)


def _push(provider: str, identity: ChannelIdentity, text: str) -> tuple[bool, str]:
    """(sent, why not) for one push to Telegram or Slack."""
    if not confirmed_here(provider, identity):
        return (
            False,
            "your Telegram account isn't confirmed on the new bot yet. Confirm it under Connections → Telegram.",
        )
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


RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))  # after the 1st, 2nd, 3rd failure
NOTES_PER_DAY = 5  # "couldn't send" notes per channel per day; after that one summary line instead
RETRY_JOB = "notify_retry"
LISTED_FOR = timedelta(days=7)


def _leave_note(
    session: Session, owner: Person, ctx: Any, provider: str, text: str, kind: str, meta: dict[str, Any]
) -> None:
    """A line in the owner's app chat about a push. At most NOTES_PER_DAY a day per channel (a flood guard);
    past that, one summary line a day saying how many are waiting. Never silent: the rows stay in the list."""
    today = datetime.now(UTC).date().isoformat()
    key = f"notify_notes:{provider}"
    state = get_state(session, key)
    count = state.get("count", 0) if state.get("day") == today else 0
    if count < NOTES_PER_DAY:
        set_state(session, key, {"day": today, "count": count + 1})
        line, note_kind = text, kind
    elif state.get("summary_day") != today:
        set_state(session, key, {"day": today, "count": count, "summary_day": today})
        waiting = len(undelivered(session, provider))
        line = f"{waiting} notifications are waiting for {CHANNEL_NAMES[provider]}. I'll keep trying."
        note_kind = "delivery_summary"
    else:
        return
    log_event(
        session, context=ctx, direction="out", content=line, audience=Audience(person_ids={owner.id}),
        meta={**meta, "kind": note_kind, "channel": provider},
    )  # fmt: skip
    audit(session, "notify", "delivery_failure_noted", provider=provider)


def undelivered(session: Session, provider: str | None = None) -> list[NotificationDelivery]:
    """Pushes still waiting for a retry, or given up on, in the last week: what the app lists as not delivered."""
    q = select(NotificationDelivery).where(
        NotificationDelivery.state.in_(("queued", "failed")),
        NotificationDelivery.created_at >= datetime.now(UTC) - LISTED_FOR,
    )
    if provider:
        q = q.where(NotificationDelivery.channel == provider)
    return list(session.scalars(q.order_by(NotificationDelivery.created_at.desc())))


def _attempt(session: Session, owner: Person, ctx: Any, d: NotificationDelivery, identity: ChannelIdentity) -> bool:
    """One try at pushing `d`. Records the outcome on the row; books the next try or gives up. True when sent."""
    provider = d.channel
    d.attempts += 1
    sent, why = _push(provider, identity, d.text)
    now = datetime.now(UTC)
    if sent:
        d.state, d.sent_at, d.next_attempt_at, d.last_error = "sent", now, None, ""
        failed_from = (d.meta or {}).get("failover_of")
        if failed_from:
            log_event(
                session, context=ctx, direction="out", audience=Audience(person_ids={owner.id}),
                content=f"I sent that on {CHANNEL_NAMES[provider]} instead.",
                meta={"kind": "delivery_failover", "channel": provider},
            )  # fmt: skip
        elif d.attempts > 1:
            log_event(
                session, context=ctx, direction="out", audience=Audience(person_ids={owner.id}),
                content=f"I sent that on {CHANNEL_NAMES[provider]} after a delay.",
                meta={"kind": "delivery_recovered", "channel": provider},
            )  # fmt: skip
        return True
    d.last_error = why
    meta = {"delivery_id": str(d.id)}
    audit(session, "notify", "delivery_failed", provider=provider, reason=why, kind=(d.meta or {}).get("kind"))
    if d.attempts <= len(RETRY_DELAYS):
        delay = RETRY_DELAYS[d.attempts - 1]
        d.state, d.next_attempt_at = "queued", now + delay
        jobs.enqueue(
            session, RETRY_JOB, {"delivery_id": str(d.id)}, run_at=d.next_attempt_at,
            dedupe_key=f"{RETRY_JOB}:{d.id}:{d.attempts}", max_attempts=1,
        )  # fmt: skip
        minutes = int(delay.total_seconds() // 60)
        _leave_note(
            session, owner, ctx, provider,
            f"I couldn't send this on {CHANNEL_NAMES[provider]}: {why} I'll try again in {minutes} minute"
            f"{'' if minutes == 1 else 's'}.",
            "delivery_failed", meta,
        )  # fmt: skip
    else:
        d.state, d.next_attempt_at = "failed", None
        _give_up(session, owner, ctx, d, f"after {d.attempts} tries: {why}", {**meta, "final": True})
    return False


def _give_up(
    session: Session, owner: Person, ctx: Any, d: NotificationDelivery, why: str, meta: dict[str, Any]
) -> None:
    """The push can no longer work on its channel: send it on another linked one when there is one, else leave it
    listed as not delivered. Either way the owner is told in the app."""
    provider = d.channel
    target = _failover(session, owner, ctx, d)
    if target is not None:
        _leave_note(
            session, owner, ctx, provider,
            f"I couldn't send this on {CHANNEL_NAMES[provider]} {why} "
            f"I'm sending it on {CHANNEL_NAMES[target]} instead.",
            "delivery_failed", meta,
        )  # fmt: skip
        return
    _leave_note(
        session, owner, ctx, provider,
        f"I couldn't send this on {CHANNEL_NAMES[provider]} {why} It is in the app, and listed as not delivered.",
        "delivery_failed", meta,
    )  # fmt: skip


def _failover(session: Session, owner: Person, ctx: Any, d: NotificationDelivery) -> str | None:
    """Hand a push that failed for good to the next channel the owner linked (Telegram to Slack or back), once.
    The new row carries the message (with a line saying why it came here) and its own retries; the old row is
    `rerouted`, so the list shows one open push, not two. Returns the new channel, or None when there is none."""
    from superaide.platform.config import get_settings

    meta = d.meta or {}
    if not get_settings().notify_failover or meta.get("failover_of"):
        return None  # one hop only: a failover that fails stays listed on its own channel
    already = session.scalar(
        select(NotificationDelivery).where(NotificationDelivery.meta["failover_of"].astext == str(d.id)).limit(1)
    )
    if already is not None:  # a second pass over the same row never sends twice
        d.state = "rerouted"
        return already.channel
    rejected = set(rejected_channels(session))
    for provider in EXTERNAL:
        identity = identity_for(session, owner, provider)
        if provider == d.channel or identity is None or provider in rejected:
            continue
        text = f"I couldn't reach you on {CHANNEL_NAMES[d.channel]}, so I'm sending this here: {d.text}"
        nd = NotificationDelivery(channel=provider, text=text, meta={**meta, "failover_of": str(d.id)}, state="queued")
        session.add(nd)
        session.flush()
        d.state, d.next_attempt_at = "rerouted", None
        audit(session, "notify", "delivery_failover", from_provider=d.channel, to_provider=provider)
        _attempt(session, owner, ctx, nd, identity)
        return provider
    return None


def acknowledge(session: Session, delivery_id: uuid.UUID) -> NotificationDelivery | None:
    """The owner saw it: stop retrying and drop it from the not-delivered list. Idempotent."""
    d = session.scalar(select(NotificationDelivery).where(NotificationDelivery.id == delivery_id).with_for_update())
    if d is None:
        return None
    if d.state != "acked":
        d.state, d.acked_at, d.next_attempt_at = "acked", datetime.now(UTC), None
        audit(session, "notify", "delivery_acked", delivery_id=d.id, provider=d.channel)
    return d


@jobs.handler(RETRY_JOB)
def retry_delivery(session: Session, job: Job) -> None:
    """Try a waiting push again. A row already sent (or given up) is left alone, so a recovered channel never
    gets the message twice. Never raises for a failed push: the row books its own next try."""
    try:
        delivery_id = uuid.UUID(str(job.payload.get("delivery_id")))
    except ValueError:
        return
    d = session.scalar(select(NotificationDelivery).where(NotificationDelivery.id == delivery_id).with_for_update())
    if d is None or d.state != "queued":
        return
    from superaide.app.accounts import owner as hired_owner

    owner = hired_owner(session)
    identity = identity_for(session, owner, d.channel) if owner is not None else None
    if owner is None:
        d.state, d.next_attempt_at = "failed", None
        return
    ctx = get_or_create_context(session, web_message(owner, ""))
    if identity is None:
        d.state, d.next_attempt_at = "failed", None
        d.last_error = f"your {CHANNEL_NAMES[d.channel]} account isn't linked"
        _give_up(session, owner, ctx, d, f"because {d.last_error}.", {"delivery_id": str(d.id), "final": True})
        return
    _attempt(session, owner, ctx, d, identity)


def notify_owner(
    session: Session, owner: Person, text: str, meta: dict[str, Any] | None = None, *, channel: str = "default"
) -> list[str]:
    """Returns where it went, e.g. ["app"] or ["app", "telegram"]. `channel` "app" keeps it in
    the app; "telegram"/"slack" also sends it there when linked; "default" follows the owner's
    interruption preference. A push that fails is retried by a job (1, 5, then 15 minutes) and the
    owner is told in the app; one that never gets through stays listed as not delivered, so a failed
    delivery is never silent."""
    ctx = get_or_create_context(session, web_message(owner, ""))
    log_event(
        session, context=ctx, direction="out", content=text, audience=Audience(person_ids={owner.id}), meta=meta or {}
    )
    via = ["app"]
    if channel == "default":
        channel = "telegram" if preferences_for(session, owner).interrupt_channel == "telegram" else "app"
    if channel in EXTERNAL:
        d = NotificationDelivery(channel=channel, text=text, meta=meta or {}, state="queued")
        session.add(d)
        session.flush()
        identity = identity_for(session, owner, channel)
        if identity is None:
            why = f"your {CHANNEL_NAMES[channel]} account isn't linked. Link it under Connections."
            d.state, d.last_error = "failed", why  # nothing to retry: it can't work until the owner links it
            audit(session, "notify", "delivery_failed", provider=channel, reason=why, kind=(meta or {}).get("kind"))
            _give_up(session, owner, ctx, d, f"because {why}", {"delivery_id": str(d.id), "final": True})
            if d.state == "rerouted":
                via += [r.channel for r in _sent_failover(session, d)]
            return via
        # Try even when the channel is marked unhealthy: a send that works is what heals it.
        if _attempt(session, owner, ctx, d, identity):
            via.append(channel)
    return via


def _sent_failover(session: Session, d: NotificationDelivery) -> list[NotificationDelivery]:
    return list(
        session.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.meta["failover_of"].astext == str(d.id), NotificationDelivery.state == "sent"
            )
        )
    )
