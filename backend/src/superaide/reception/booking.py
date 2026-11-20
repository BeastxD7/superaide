"""Booking time for a visitor (#496): "can we speak Thursday?" from someone Ava doesn't know.

As everywhere in reception, the model only proposes (`ReceptionTurn.schedule`: a length, the day words the visitor used,
a time zone they stated, a slot number they chose); code decides everything else:

- Whether booking is on at all: Google Calendar connected, the calendar skill on, calendar actions allowed by the owner,
  and the owner's "meeting times" setting above 0. When it is not, the visitor gets one fixed sentence, whatever the
  reason (a probe cannot tell "not connected" from "calendar full").
- Which times are offered: `slots.py`, from Google's free/busy (never event details), the owner's working hours, a
  buffer, a lead time, the owner's away date and active status, and times already held for someone else. The model never
  sees the calendar, and the visitor sees two or three slots, never the grid. The same slots are re-offered while the
  request is live, so asking again does not walk the calendar.
- What the visitor hears: fixed templates. The model's own reply is dropped on a scheduling turn.
- Booking: the visitor picks a slot by number. Code re-checks the time is still free, opens an approval card for the
  owner (kind `visitor_booking`; there is no path that skips the card: not a stage, not a mandate) and holds the slot
  in `booking_request`. Nothing is written to the calendar yet.
- On approval the executor re-checks free/busy, creates a private event (a fixed title with the visitor's first name,
  a fixed description, the visitor as a guest only when they gave an email) and tells the visitor in a fixed sentence.
  On decline, expiry or a taken slot the hold is released and the visitor is told so.
- Limits: 3 requests per visitor per day, one open at a time, 10 open in all.

What the visitor typed is data: their name is cut to letters and quoted as unverified; none of their words reach the
notification, the event description or a prompt other than their own conversation."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.approvals.binding import EXPIRED_BY, seal
from superaide.approvals.models import Approval
from superaide.identity.models import Person
from superaide.integrations.calendar import settings as cal_settings
from superaide.integrations.calendar import skill as calendar
from superaide.integrations.google import client as google
from superaide.intentions.service import owner_zone
from superaide.intentions.when import find_moment, zone
from superaide.jobs import queue as jobs
from superaide.memory.audience import Audience
from superaide.memory.models import Context
from superaide.platform.audit import audit
from superaide.policy.registry import DISCARDERS, EXECUTORS
from superaide.reception import slots
from superaide.reception.models import BookingRequest

log = logging.getLogger(__name__)

KIND = "visitor_booking"
PER_DAY = 3  # requests one visitor can open in 24 hours
OPEN_CAP = 10  # requests waiting for the owner or for the visitor, all visitors
OFFER_TTL = timedelta(hours=2)  # the visitor has this long to pick one of the offered times
HOLD_TTL = timedelta(hours=24)  # a picked time is held this long for the owner (or until the card's own expiry)
LEAD = timedelta(hours=2)
HORIZON_DAYS = 7
MINUTES = (15, 30, 45, 60)  # lengths a visitor can ask for; anything else becomes 30
APPROVALS_PATH = "/dashboard/approvals"

UNAVAILABLE = "I can't offer meeting times right now, but I can take a message and pass it on."
NO_SLOTS = "I don't have a time to offer in the next few days. I can take a message and pass it on."
OFFER = (
    "{who} could do {times}{zone}. Which would you prefer? Reply with 1, 2 or 3. Nothing is booked until "
    "{who} confirms."
)
CHOSEN = (
    "Thank you. I've asked {who} to confirm {when}{zone}, and I'll tell you here once they answer. "
    "It is not booked until then."
)
NOT_OFFERED = "I don't have times on offer for you right now. Tell me when you'd like to meet and I'll look."
TAKEN = "That time is no longer free. Tell me when else you'd like to meet and I'll look."
ALREADY = "I've already asked {who} about {when}{zone}. I'll tell you here when they answer."
LIMIT = "I can only look for times a few times a day. I can take a message and pass it on."
CONFIRMED = "Good news: {who} confirmed {when}{zone}. You'll find it in your calendar if you gave me an email address."
DECLINED = "{who} can't make {when}{zone}. If you like, leave a message and I'll pass it on."
EXPIRED = "I didn't get an answer about {when}{zone} in time, so I've released it. Tell me if you'd like to try again."
FAILED = "I couldn't book {when}{zone}. If you like, leave a message and I'll pass it on."

_MARKUP = re.compile(r"<[^>]*>|\S+://\S*|\S+@\S+|www\.\S+")
_LETTERS = re.compile(r"[^A-Za-z'’ -]")


def clean_name(raw: str) -> str:
    """A visitor's own claim, reduced to letters: it can be quoted as unverified but never carries markup or links."""
    text = _MARKUP.sub(" ", raw or "")
    words = _LETTERS.sub("", text).split()
    return " ".join(words[:2])[:40]


def clean_email(raw: str) -> str:
    from email_validator import EmailNotValidError, validate_email

    if not raw or len(raw) > 254:
        return ""
    try:
        return validate_email(raw.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return ""


def now_utc() -> datetime:
    return datetime.now(UTC)  # one place to freeze in tests


def available(session: Session) -> bool:
    """Whether Ava may offer a visitor meeting times at all. The owner's switches, read in code."""
    try:
        return (
            calendar.connected()
            and calendar.enabled(session)
            and calendar.actions_on(session)
            and calendar.can_write(session)
            and int(cal_settings.load(session)["visitor_slots"]) > 0
        )
    except Exception:  # a settings hiccup means "not offered", never an error to a visitor
        log.warning("couldn't tell whether booking is on", exc_info=True)
        return False


def who(session: Session, owner: Person) -> str:
    from superaide.reception import ask

    return ask.owner_ref(session, owner)


def _zone_label(tz_name: str) -> str:
    return " (UTC)" if tz_name == "UTC" else f" ({tz_name} time)"


def _when(b: BookingRequest) -> str:
    return slots.render(_interval(b.slots[b.chosen or 0]), b.tz_name)


def _interval(slot: dict[str, str]) -> slots.Interval:
    return datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"])


def _hold(session: Session) -> list[slots.Interval]:
    """Times already offered or held for other visitors: not offered again."""
    out: list[slots.Interval] = []
    rows = session.scalars(
        select(BookingRequest).where(
            BookingRequest.state.in_(("proposed", "pending")), BookingRequest.expires_at > now_utc()
        )
    )
    for b in rows:
        picks = [b.slots[b.chosen]] if b.state == "pending" and b.chosen is not None else b.slots
        out.extend(_interval(s) for s in picks)
    return out


def _busy(session: Session, owner: Person, start: datetime, end: datetime) -> list[slots.Interval]:
    """Busy intervals only: Google's free/busy (no titles), the owner's away date and an active status."""
    from superaide.presence import api as presence
    from superaide.triage.models import TriageSettings

    token = calendar._token()
    busy = list(google.free_busy(token, start=start, end=end))
    ts = session.scalar(select(TriageSettings).where(TriageSettings.person_id == owner.id))
    if ts is not None and ts.away_until is not None and ts.away_until > start:
        busy.append((start, ts.away_until))
    state = presence.active(session, owner.id)
    if state is not None:
        busy.append((state.valid_from, state.valid_until))
    return busy


def _open_total(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(BookingRequest)
            .where(BookingRequest.state.in_(("proposed", "pending")), BookingRequest.expires_at > now_utc())
        )
        or 0
    )


def _live(session: Session, identity_id: uuid.UUID) -> BookingRequest | None:
    return session.scalar(
        select(BookingRequest)
        .where(
            BookingRequest.identity_id == identity_id,
            BookingRequest.state.in_(("proposed", "pending")),
            BookingRequest.expires_at > now_utc(),
        )
        .order_by(BookingRequest.created_at.desc())
        .limit(1)
    )


def _today(session: Session, identity_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(BookingRequest)
            .where(
                BookingRequest.identity_id == identity_id, BookingRequest.created_at >= now_utc() - timedelta(hours=24)
            )
        )
        or 0
    )


def _wanted_day(words: str, tz_name: str, now: datetime) -> date | None:
    if not words.strip():
        return None
    found = find_moment(words.strip()[:80], now.astimezone(zone(tz_name)))
    return found[0].astimezone(zone(tz_name)).date() if found else None


def handle(
    session: Session,
    owner: Person,
    *,
    identity_id: uuid.UUID,
    ctx: Context,
    provider: str,
    ask: Any,
    visitor_name: str = "",
) -> str:
    """The visitor's reply for a scheduling turn. `ask` is the model's `ScheduleAsk`: only a proposal."""
    expire_old(session)
    if not available(session):
        audit(session, "reception", "booking_unavailable", identity_id=identity_id)
        return UNAVAILABLE
    name = who(session, owner)
    live = _live(session, identity_id)
    if ask.pick:
        return _choose(session, owner, live, int(ask.pick), name)
    if live is not None and live.state == "pending":
        return ALREADY.format(who=name, when=_when(live), zone=_zone_label(live.tz_name))
    if live is not None and not ask.day.strip():  # the same times again: asking twice never shows more of the calendar
        return _offer_text(live, name)
    if live is not None:  # a different day: the old offer is let go, and this counts as a new request below
        live.state, live.decided_at = "expired", now_utc()
    if _today(session, identity_id) >= PER_DAY:
        audit(session, "reception", "booking_refused", identity_id=identity_id, reason="visitor_cap")
        return LIMIT
    if _open_total(session) >= OPEN_CAP:
        audit(session, "reception", "booking_refused", identity_id=identity_id, reason="open_cap")
        return UNAVAILABLE
    cfg = cal_settings.load(session)
    tz_name = slots.visitor_zone(ask.timezone)
    minutes = ask.minutes if ask.minutes in MINUTES else 30
    now = now_utc()
    owner_tz = zone(owner_zone(session, owner))
    horizon = min(HORIZON_DAYS, int(cfg["ahead_days"]))
    try:
        busy = _busy(session, owner, now, now + timedelta(days=horizon + 2))
    except google.GoogleError, OSError:
        log.warning("booking: couldn't read free/busy", exc_info=True)
        return UNAVAILABLE
    found = slots.candidates(
        now=now,
        tz=owner_tz,
        minutes=minutes,
        day_start=int(cfg["day_start"]),
        day_end=int(cfg["day_end"]),
        taken=[*busy, *_hold(session)],
        buffer=int(cfg["buffer_minutes"]),
        lead=LEAD,
        days=horizon,
    )
    chosen = slots.choose(
        found, zone(tz_name), limit=int(cfg["visitor_slots"]), want=_wanted_day(ask.day, tz_name, now)
    )
    if not chosen:
        audit(session, "reception", "booking_no_slots", identity_id=identity_id)
        return NO_SLOTS
    b = BookingRequest(
        identity_id=identity_id,
        context_id=ctx.id,
        provider=provider,
        minutes=minutes,
        tz_name=tz_name,
        visitor_name=clean_name(visitor_name),
        visitor_email=clean_email(ask.email),
        slots=[{"start": s.isoformat(), "end": e.isoformat()} for s, e in chosen],
        state="proposed",
        expires_at=now + OFFER_TTL,
    )
    session.add(b)
    session.flush()
    audit(session, "reception", "booking_offered", booking_id=b.id, slots=len(chosen))
    return _offer_text(b, name)


def _offer_text(b: BookingRequest, name: str) -> str:
    shown = [slots.render(_interval(s), b.tz_name) for s in b.slots]
    listed = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " or " + shown[-1]
    numbered = "; ".join(f"{i}. {t}" for i, t in enumerate(shown, 1))
    return OFFER.format(who=name, times=numbered if len(shown) > 1 else listed, zone=_zone_label(b.tz_name))


def _choose(session: Session, owner: Person, live: BookingRequest | None, pick: int, name: str) -> str:
    if live is None or live.state != "proposed":
        return NOT_OFFERED
    if not 1 <= pick <= len(live.slots):
        return _offer_text(live, name)
    start, end = _interval(live.slots[pick - 1])
    try:
        busy = _busy(session, owner, start - timedelta(hours=1), end + timedelta(hours=1))
    except google.GoogleError, OSError:
        return UNAVAILABLE
    margin = timedelta(minutes=int(cal_settings.load(session)["buffer_minutes"]))
    if start < now_utc() + LEAD or not slots._clear(start, end, busy, margin):
        live.state, live.decided_at = "expired", now_utc()
        audit(session, "reception", "booking_slot_taken", booking_id=live.id)
        return TAKEN
    live.chosen = pick - 1
    owner_name = zone(owner_zone(session, owner))
    local = start.astimezone(owner_name)
    where = {"telegram": "Telegram", "slack": "Slack"}.get(live.provider, live.provider.capitalize())
    claimed = f"“{live.visitor_name}”" if live.visitor_name else "someone who gave no name"
    summary = (
        f"Meet {claimed} (unverified, on {where}): {local:%a %d %b, %H:%M}–{end.astimezone(owner_name):%H:%M} "
        f"({owner_name})" + (f", inviting {live.visitor_email}" if live.visitor_email else "")
    )
    approval = Approval(
        kind=KIND,
        payload={
            "summary": summary,
            "booking_id": str(live.id),
            "identity_id": str(live.identity_id),
            "context_id": str(live.context_id),
            "provider": live.provider,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "visitor": live.visitor_name,
            "email": live.visitor_email,
            "tainted": True,  # a visitor's words are in it: never batched, never skipped by a mandate
        },
        requested_by_person_id=None,
    )
    seal(approval)
    session.add(approval)
    session.flush()
    live.approval_id = approval.id
    live.state = "pending"
    live.expires_at = min(approval.expires_at, now_utc() + HOLD_TTL) if approval.expires_at else now_utc() + HOLD_TTL
    audit(session, "policy", "approval_requested", kind=KIND, approval_id=approval.id)
    audit(session, "reception", "booking_requested", booking_id=live.id, approval_id=approval.id)
    jobs.enqueue(
        session,
        "booking_expire",
        {"booking_id": str(live.id)},
        run_at=live.expires_at + timedelta(seconds=1),
        dedupe_key=f"booking:{live.id}",
    )
    _ping(session, owner, live)
    return CHOSEN.format(who=name, when=slots.render((start, end), live.tz_name), zone=_zone_label(live.tz_name))


def _ping(session: Session, owner: Person, b: BookingRequest) -> None:
    """Fixed text only, like a visitor question: the card holds the details. Held back in quiet hours or a status."""
    from superaide.memory.knowledge.profile import in_quiet_hours
    from superaide.memory.knowledge.store import preferences_for
    from superaide.presence import api as presence
    from superaide.proactive.notify import notify_owner

    now = now_utc()
    held = in_quiet_hours(preferences_for(session, owner), now) or presence.active(session, owner.id, now) is not None
    text = f"Someone I don't know asked to book time with you. Open Approvals to decide: {APPROVALS_PATH}"
    meta = {"kind": "booking_request", "booking_id": str(b.id), "source": f"notification:{b.id}"}
    try:
        notify_owner(session, owner, text, meta, channel="app" if held else "default")
    except Exception:
        log.warning("booking: couldn't notify the owner", exc_info=True)


# --- telling the visitor ---------------------------------------------------------------------------------


def _send(session: Session, b: BookingRequest, text: str, *, kind: str) -> bool:
    """Send `text` into the conversation the request came from and record it. True when the platform took it."""
    from superaide.channels.health import DeliveryFailed
    from superaide.memory.store import log_event
    from superaide.proactive import notify

    ctx = session.get(Context, b.context_id)
    messenger = notify.messenger_for(b.provider)
    if ctx is None or ctx.kind != "dm" or ctx.provider != b.provider or messenger is None:
        return False
    try:
        ok = messenger.send_to_context(ctx, text)
    except DeliveryFailed as exc:
        audit(session, "reception", "booking_relay_failed", booking_id=b.id, reason=exc.kind)
        return False
    except Exception as exc:
        audit(session, "reception", "booking_relay_failed", booking_id=b.id, reason=type(exc).__name__)
        return False
    if ok is False:
        audit(session, "reception", "booking_relay_failed", booking_id=b.id, reason="refused")
        return False
    log_event(
        session, context=ctx, direction="out", content=text, audience=Audience(has_unknown=True),
        meta={"booking": kind, "booking_id": str(b.id)},
    )  # fmt: skip
    return True


def _finish(session: Session, b: BookingRequest, state: str, template: str, owner: Person | None = None) -> None:
    """Release the hold (state is no longer proposed/pending) and tell the visitor, once."""
    if b.state not in ("proposed", "pending"):
        return
    b.state, b.decided_at = state, now_utc()
    if b.chosen is None:
        return
    name = who(session, owner) if owner is not None else "The person I work for"
    _send(session, b, template.format(who=name, when=_when(b), zone=_zone_label(b.tz_name)), kind=state)


def _owner(session: Session) -> Person | None:
    from superaide.app.accounts import owner as instance_owner

    return instance_owner(session)


def expire_old(session: Session, now: datetime | None = None) -> None:
    """Offers nobody picked and holds nobody answered are released; the visitor is told about a held time."""
    now = now or now_utc()
    for b in session.scalars(
        select(BookingRequest).where(
            BookingRequest.state.in_(("proposed", "pending")), BookingRequest.expires_at <= now
        )
    ):
        was_pending = b.state == "pending"
        _finish(session, b, "expired", EXPIRED, _owner(session))
        if was_pending and b.approval_id is not None:
            a = session.get(Approval, b.approval_id)
            if a is not None and a.status == "pending":
                a.status, a.decided_by, a.decided_at = "rejected", EXPIRED_BY, now
                a.payload = {**(a.payload or {}), "result": "Not run: this request expired before it was answered."}
        audit(session, "reception", "booking_expired", booking_id=b.id)


@jobs.handler("booking_expire")
def _expire_job(session: Session, job: Any) -> None:
    expire_old(session)


# --- what the owner's answer does --------------------------------------------------------------------------


def _row(session: Session, approval: Approval) -> BookingRequest | None:
    try:
        return session.get(BookingRequest, uuid.UUID(str((approval.payload or {}).get("booking_id"))))
    except ValueError:
        return None


def _event_body(b: BookingRequest, start: datetime, end: datetime) -> dict[str, Any]:
    first = (b.visitor_name.split() or [""])[0]
    body: dict[str, Any] = {
        "summary": f"Call with {first} (booked by Ava)" if first else "Call (booked by Ava)",
        "description": "Booked by Ava after you approved it. The visitor's identity was not verified.",
        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        "visibility": "private",
        "guestsCanSeeOtherGuests": False,
        "guestsCanInviteOthers": False,
        "guestsCanModify": False,
    }
    if b.visitor_email:
        body["attendees"] = [{"email": b.visitor_email}]
    return body


def execute(session: Session, approval: Approval) -> str:
    """The owner approved this exact time: re-check it is still free, create the event, tell the visitor."""
    from superaide.policy.killswitch import is_frozen

    b = _row(session, approval)
    if b is None or b.state != "pending" or b.approval_id != approval.id:
        return "Not booked: this request is no longer open."
    if is_frozen(session):
        return "Not booked: Ava is paused. Resume her in the app and ask the visitor to try again."
    owner = _owner(session)
    when_text = _when(b)
    if not calendar.connected() or not calendar.can_write(session) or owner is None:
        _finish(session, b, "failed", FAILED, owner)
        return "Approved, but Google Calendar isn't connected with calendar actions any more. Nothing changed."
    start, end = _interval(b.slots[b.chosen or 0])
    if start < now_utc():
        _finish(session, b, "expired", EXPIRED, owner)
        return f"Not booked: {when_text} has already passed."
    try:
        busy = calendar_busy_only(start - timedelta(hours=1), end + timedelta(hours=1))
        if not slots._clear(start, end, busy, timedelta(0)):
            _finish(session, b, "failed", FAILED, owner)
            audit(session, "reception", "booking_slot_taken", booking_id=b.id, approval_id=approval.id)
            return "Not booked: something was added to your calendar at that time since the request. Nothing changed."
        made = google.insert_event(calendar._token(), _event_body(b, start, end), notify=bool(b.visitor_email))
    except google.GoogleError as exc:
        _finish(session, b, "failed", FAILED, owner)
        return f"Approved, but Google said: {google.tool_text(exc)} Nothing changed."
    b.event_id, b.state, b.decided_at = str(made.get("id", "")), "approved", now_utc()
    sent = _send(
        session, b, CONFIRMED.format(who=who(session, owner), when=when_text, zone=_zone_label(b.tz_name)),
        kind="approved",
    )  # fmt: skip
    audit(session, "reception", "booking_created", booking_id=b.id, approval_id=approval.id, event_id=b.event_id)
    return f"Booked {when_text}{_zone_label(b.tz_name)}." + (
        "" if sent else " I could not tell the visitor: tell them."
    )


def calendar_busy_only(start: datetime, end: datetime) -> list[slots.Interval]:
    return list(google.free_busy(calendar._token(), start=start, end=end))


def discard(session: Session, approval: Approval) -> None:
    """The owner said no: release the hold and tell the visitor."""
    b = _row(session, approval)
    if b is not None and b.state == "pending":
        _finish(session, b, "declined", DECLINED, _owner(session))
        audit(session, "reception", "booking_declined", booking_id=b.id, approval_id=approval.id)


EXECUTORS[KIND] = execute
DISCARDERS[KIND] = discard
