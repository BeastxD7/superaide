"""Reading a whole channel, not only the messages that mention Ava (#509). Opt-in per channel, off by default.

The owner turns it on for one channel at a time (Connections > Reading whole channels). What changes for that
channel, and only that one:

- Every message of the room is stored in the event log, the way an @-mention already is. Nothing else happens when
  it arrives: no model call, no reply, no memory, no reminder, no halt word, no link code, no notice to the owner,
  no claim review. It is stored, that is all.
- It carries the audience of the room (`audience_has_unknown`), so the audience gate keeps it out of every other
  context. It never names the owner in its audience, so the nightly summary job does not read it, and it is
  excluded from conversation history (`memory.store.recent_events`), from recall for anyone but the owner in their
  private chat, and from standing-order previews.
- It reaches a prompt only through the owner's private-chat tools (`channel_activity`, `recall`), as quoted text
  from other people, and the turn is then marked tainted: information, never instructions.
- It is capped (messages per hour per channel, characters per message), it ages out after the channel's retention
  (default 30 days), and the owner can erase everything stored from a channel at any time.

Who wrote a message is only read, never created: an unknown Slack user gets no identity row from a room message.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from superaide.channels.base import InboundMessage
from superaide.channels.models import AmbientChannel
from superaide.identity.models import ChannelIdentity, Person
from superaide.identity.resolver import find_identity
from superaide.memory.models import Context, Event
from superaide.memory.store import get_or_create_context, log_room_event, seen_before
from superaide.platform.audit import audit
from superaide.platform.db.session import rows_affected

log = logging.getLogger(__name__)

PROVIDERS = ("slack",)  # Telegram bots only see commands and mentions in groups unless privacy mode is off
MAX_TEXT = 4000  # characters kept of one message
RETENTION_CHOICES = (7, 30, 90, 365)
DEFAULT_RETENTION = 30
HOUR = timedelta(hours=1)
SLACK_ID_RE = re.compile(r"^[CG][A-Z0-9]{2,40}$")


def meta_marks_ambient(meta: Any) -> bool:
    return isinstance(meta, dict) and bool(meta.get("ambient"))


# --- the owner's switch ---------------------------------------------------------------------------------------


def find(session: Session, provider: str, channel_id: str) -> AmbientChannel | None:
    """A Slack channel id is unique in its workspace, and one Ava is installed in one workspace."""
    return session.scalar(
        select(AmbientChannel).where(AmbientChannel.provider == provider, AmbientChannel.external_id == channel_id)
    )


def enabled_rows(session: Session) -> list[AmbientChannel]:
    return list(
        session.scalars(
            select(AmbientChannel).where(AmbientChannel.enabled.is_(True)).order_by(AmbientChannel.created_at)
        )
    )


def any_enabled(session: Session) -> bool:
    return session.scalar(select(AmbientChannel.id).where(AmbientChannel.enabled.is_(True)).limit(1)) is not None


def set_enabled(
    session: Session,
    *,
    provider: str,
    tenant_scope: str,
    channel_id: str,
    title: str,
    enabled: bool,
    owner_id: uuid.UUID,
    retention_days: int | None = None,
) -> AmbientChannel:
    row = find(session, provider, channel_id)
    if row is None:
        row = AmbientChannel(
            provider=provider, tenant_scope=tenant_scope, external_id=channel_id, title=title[:200], enabled=False
        )
        session.add(row)
    if title:
        row.title = title[:200]
    if retention_days is not None:
        row.retention_days = retention_days
    if enabled and not row.enabled:
        row.enabled_at, row.enabled_by_person_id, row.paused_until = datetime.now(UTC), owner_id, None
    row.enabled = enabled
    session.flush()
    audit(
        session,
        "owner",
        "ambient_channel_on" if enabled else "ambient_channel_off",
        provider=provider,
        channel=channel_id,
        retention_days=row.retention_days,
    )
    return row


# --- the volume cap ----------------------------------------------------------------------------------------------

_window: dict[tuple[uuid.UUID, str, str], tuple[datetime, int]] = {}
_window_lock = threading.Lock()


def _under_cap(agent_id: uuid.UUID, provider: str, channel_id: str, per_hour: int) -> bool:
    """A fixed one-hour window per channel, counted in this process: an overload is cut off without a database
    round trip for every message. A restart forgets the count, which can only let one more hour through."""
    now = datetime.now(UTC)
    key = (agent_id, provider, channel_id)
    with _window_lock:
        start, n = _window.get(key, (now, 0))
        if now - start >= HOUR:
            start, n = now, 0
        if n >= per_hour:
            _window[key] = (start, n)
            return False
        _window[key] = (start, n + 1)
        return True


def reset_caps() -> None:
    with _window_lock:
        _window.clear()


# --- ingestion -------------------------------------------------------------------------------------------------


def bot_user(msg: InboundMessage) -> str:
    """Our bot's Slack user id as the event says it, so a message that mentions Ava is left to the mention path."""
    for auth in msg.raw.get("authorizations") or []:
        if isinstance(auth, dict) and auth.get("user_id"):
            return str(auth["user_id"])
    return ""


def adopt(session: Session, context: Context, msg: InboundMessage) -> None:
    """A message that mentions Ava arrives twice (`message` and `app_mention`). If the plain copy was stored first,
    remove it so the mention is handled as the new message it is, and not dropped as a redelivery."""
    if not msg.message_id:
        return
    session.execute(
        delete(Event).where(
            Event.context_id == context.id,
            Event.direction == "in",
            Event.external_id == msg.message_id,
            Event.meta["ambient"].astext == "true",
        )
    )


def ingest(session: Session, msg: InboundMessage, *, per_hour: int) -> str:
    """Store one room message if the owner let Ava read this channel. Returns what happened:
    off | mention | capped | duplicate | stored. Never raises for a normal reason and never calls a model."""
    row = find(session, msg.provider, msg.chat_id)
    if row is None or not row.enabled:
        return "off"
    if (bot := bot_user(msg)) and f"<@{bot}" in msg.text:
        return "mention"
    ctx = get_or_create_context(session, msg)
    if ctx.kind != "group":
        return "off"
    if seen_before(session, ctx, msg.message_id):
        return "duplicate"
    now = datetime.now(UTC)
    if row.paused_until is not None and row.paused_until > now:
        return "capped"
    if not _under_cap(row.agent_id, row.provider, row.external_id, per_hour):
        row.paused_until = now + HOUR
        audit(session, "pipeline", "ambient_capped", provider=row.provider, channel=row.external_id, per_hour=per_hour)
        return "capped"
    identity = find_identity(session, msg.provider, msg.tenant_scope, msg.sender_id)
    person = _linked_person(session, identity)
    if not ctx.title and row.title:
        ctx.title = row.title
    try:
        with session.begin_nested():  # a redelivery racing this one hits the unique index
            log_room_event(
                session, context=ctx, content=msg.text[:MAX_TEXT], identity_id=identity.id if identity else None,
                person_id=person.id if person else None, external_id=msg.message_id, thread_id=msg.thread_id,
                verified=msg.verified, meta={"ambient": True, "sender": msg.sender_id},
            )  # fmt: skip
    except IntegrityError:
        return "duplicate"
    row.last_heard_at = now
    return "stored"


def _linked_person(session: Session, identity: ChannelIdentity | None) -> Person | None:
    """Who the account is linked to, for the label only. An account from another company is never a colleague."""
    if identity is None or identity.person_id is None or identity.lifecycle != "active":
        return None
    if "external" in (identity.risk_flags or []):
        return None
    person = session.get(Person, identity.person_id)
    return person if person is not None and person.status == "active" else None


# --- keeping and erasing -----------------------------------------------------------------------------------------


def _channel_events(session: Session, row: AmbientChannel) -> Any:
    ids = select(Context.id).where(
        Context.provider == row.provider, Context.external_id == row.external_id, Context.kind == "group"
    )
    return Event.context_id.in_(ids), Event.meta["ambient"].astext == "true"


def stored_count(session: Session, row: AmbientChannel) -> int:
    where_ctx, where_ambient = _channel_events(session, row)
    return int(session.scalar(select(func.count()).select_from(Event).where(where_ctx, where_ambient)) or 0)


def erase(session: Session, row: AmbientChannel) -> int:
    """Delete everything stored from this channel by reading it. (Messages that mentioned Ava are part of her
    conversations and are not touched here.)"""
    where_ctx, where_ambient = _channel_events(session, row)
    n = rows_affected(session.execute(delete(Event).where(where_ctx, where_ambient)))
    audit(session, "owner", "ambient_channel_erased", provider=row.provider, channel=row.external_id, messages=n)
    return int(n)


def roll_off(session: Session) -> int:
    """Delete room messages older than their channel's retention. Run by the daily job, for every channel
    that has a row (switched off or not: what was kept still ages out)."""
    total = 0
    for row in session.scalars(select(AmbientChannel)):
        days = row.retention_days if row.retention_days > 0 else DEFAULT_RETENTION
        where_ctx, where_ambient = _channel_events(session, row)
        cutoff = datetime.now(UTC) - timedelta(days=days)
        n = rows_affected(session.execute(delete(Event).where(where_ctx, where_ambient, Event.created_at < cutoff)))
        if n:
            audit(
                session, "memory", "ambient_rolled_off", provider=row.provider, channel=row.external_id,
                messages=n, older_than_days=days,
            )  # fmt: skip
        total += int(n)
    return total
