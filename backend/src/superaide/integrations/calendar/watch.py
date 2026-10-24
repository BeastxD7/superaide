"""Google Calendar push: Google calls us when the owner's calendar changes (events.watch).

Behind GOOGLE_CALENDAR_PUSH (default off) and a public https address (PUBLIC_BASE_URL). Each
instance with the calendar skill on and Google connected gets one watch channel on its primary
calendar. A notification only says "something changed": it runs the meeting scan for that owner
at once, so a meeting added or moved in the next couple of hours gets its prep. The half-hourly
scan keeps running whatever happens here: it is the fallback when push is off, a watch can't be
made, or a notification is lost.

Verification: the channel id starts with the instance's id (so the webhook knows whose it is
before any data is read), and Google echoes back the per-channel token we chose,
X-Goog-Channel-Token. That token is an HMAC of the channel id under the vault's master key:
nothing secret is stored, and a stolen database alone can't forge one. The resource id must match
too, and the channel must be active and unexpired.

Renewal: channels expire (we ask for 7 days). A job a day before expiry opens a new channel and
stops the old one.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.integrations.google import client as google
from superaide.jobs.queue import enqueue, handler
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.models import CalendarWatch, Person
from superaide.platform.tenancy.context import InstanceScope, scope_or_default

log = logging.getLogger(__name__)

RENEW_KIND = "renew_calendar_watch"
TTL = timedelta(days=7)
RENEW_BEFORE = timedelta(days=1)
PATH = "/webhooks/google/calendar"


def base_url() -> str:
    s = get_settings()
    return (s.public_base_url or s.telegram_webhook_base_url).rstrip("/")


def push_on() -> bool:
    return get_settings().google_calendar_push and base_url().startswith("https://")


def channel_token(channel_id: str) -> str:
    from superaide.vault.crypto import master_key

    key, _ = master_key()
    return hmac.new(key, f"google-calendar-watch:{channel_id}".encode(), hashlib.sha256).hexdigest()


def token_ok(channel_id: str, received: str | None) -> bool:
    return bool(received) and hmac.compare_digest(channel_token(channel_id), received or "")


def new_channel_id(scope: InstanceScope) -> str:
    """<instance id, 32 hex>-<random>: within Google's [A-Za-z0-9-_+/=]{1,64}."""
    return f"{scope.agent_id.hex}-{secrets.token_hex(12)}"


def agent_of(channel_id: str) -> uuid.UUID | None:
    head = channel_id.split("-", 1)[0]
    try:
        return uuid.UUID(hex=head) if len(head) == 32 else None
    except ValueError:
        return None


def active(session: Session, owner: Person) -> CalendarWatch | None:
    return session.scalar(
        select(CalendarWatch)
        .where(CalendarWatch.owner_person_id == owner.id, CalendarWatch.status == "active")
        .order_by(CalendarWatch.expires_at.desc())
    )


def _token() -> str:
    from superaide.vault.store import runtime_settings

    s = runtime_settings()
    return google.access_token(s.google_client_id, s.google_client_secret, s.google_refresh_token)


def start(session: Session, owner: Person, now: datetime | None = None) -> CalendarWatch | None:
    """Open a watch channel for this owner's calendar. None (and an audit row) if Google refuses."""
    now = now or datetime.now(UTC)
    channel_id = new_channel_id(scope_or_default())
    try:
        made = google.watch(
            _token(), channel_id=channel_id, address=base_url() + PATH, channel_token=channel_token(channel_id),
            ttl_seconds=int(TTL.total_seconds()),
        )  # fmt: skip
    except google.GoogleError as exc:
        audit(session, "proactive", "calendar_watch_failed", reason=str(exc)[:200])
        return None
    try:
        expires = datetime.fromtimestamp(int(made["expiration"]) / 1000, UTC)
    except KeyError, ValueError, TypeError:
        expires = now + TTL
    row = CalendarWatch(
        owner_person_id=owner.id, channel_id=channel_id, resource_id=str(made.get("resourceId") or "")[:200],
        expires_at=expires,
    )  # fmt: skip
    session.add(row)
    session.flush()
    enqueue(session, RENEW_KIND, {"watch_id": str(row.id)}, run_at=max(now, expires - RENEW_BEFORE),
            dedupe_key=f"calwatch:{row.id}")  # fmt: skip
    audit(session, "proactive", "calendar_watch_started", channel_id=channel_id, expires_at=expires.isoformat())
    return row


def stop(session: Session, row: CalendarWatch) -> None:
    """Stop a channel (best effort: an expired or unknown channel is just forgotten)."""
    row.status = "stopped"
    try:
        google.stop_channel(_token(), channel_id=row.channel_id, resource_id=row.resource_id)
    except google.GoogleError:
        log.info("couldn't stop calendar channel %s; it expires by itself", row.channel_id)
    audit(session, "proactive", "calendar_watch_stopped", channel_id=row.channel_id)


def ensure(session: Session, owner: Person, now: datetime | None = None) -> CalendarWatch | None:
    """Keep exactly one live channel while push is on and the calendar is readable; none otherwise.
    Called from each meeting scan, so it follows the owner connecting or disconnecting."""
    from superaide.integrations.calendar import skill as calendar

    now = now or datetime.now(UTC)
    current = active(session, owner)
    wanted = push_on() and calendar.enabled(session) and calendar.connected()
    if not wanted:
        if current is not None:
            current.status = "stopped"  # nothing to call Google with, or push is off: forget it
            audit(session, "proactive", "calendar_watch_stopped", channel_id=current.channel_id)
        return None
    if current is not None and current.expires_at > now + timedelta(minutes=5):
        return current
    if current is not None:
        current.status = "expired"
    return start(session, owner, now)


@handler(RENEW_KIND)
def renew(session: Session, job: object) -> None:
    payload = getattr(job, "payload", {}) or {}
    row = session.get(CalendarWatch, uuid.UUID(str(payload.get("watch_id"))))
    if row is None or row.status != "active":
        return
    owner = session.get(Person, row.owner_person_id)
    if owner is None:
        return
    from superaide.integrations.calendar import skill as calendar

    if not (push_on() and calendar.enabled(session) and calendar.connected()):
        row.status = "stopped"
        return
    fresh = start(session, owner)
    if fresh is not None:
        stop(session, row)


def handle_notification(channel_id: str | None, token: str | None, resource_id: str | None, state: str | None) -> bool:
    """A call from Google. True when it is genuine (and acted on); False when it isn't, which the
    webhook answers with 401. Nothing is read or queued for an unverified call."""
    from superaide.platform.db.session import session_scope
    from superaide.platform.tenancy.context import use_instance
    from superaide.platform.tenancy.models import AgentInstance

    agent = agent_of(channel_id or "")
    if agent is None or not channel_id or not token_ok(channel_id, token):
        return False
    with session_scope() as s:
        instance = s.get(AgentInstance, agent)
        scope = InstanceScope(tenant_id=instance.tenant_id, agent_id=instance.id) if instance else None
    if scope is None:
        return False
    now = datetime.now(UTC)
    with use_instance(scope), session_scope() as s:
        row = s.scalar(select(CalendarWatch).where(CalendarWatch.channel_id == channel_id))
        if row is None or row.status != "active" or row.expires_at <= now:
            return False
        if not resource_id or not hmac.compare_digest(row.resource_id, resource_id):
            return False
        if (state or "").lower() == "exists":  # "sync" is Google saying hello to a new channel
            bucket = int(now.timestamp()) // 30  # a burst of changes becomes one scan
            enqueue(s, "scan_meetings", {"owner_id": str(row.owner_person_id)}, run_at=now,
                    dedupe_key=f"meetings-push:{row.owner_person_id}:{bucket}")  # fmt: skip
        audit(s, "proactive", "calendar_push_received", state=state)
    return True
