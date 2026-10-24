"""The calendar_events tool: the owner's Google Calendar as citable sources."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from superaide.connectors import google
from superaide.contracts.events import CalendarEventsRead, bus
from superaide.intentions.service import owner_zone
from superaide.intentions.when import find_moment, zone
from superaide.platform.audit import audit
from superaide.platform.db.models import Person
from superaide.policy.actions import EXECUTORS, TOOL_RULES, decide
from superaide.skills.registry import active
from superaide.vault.store import runtime_settings

SKILL_ID = "superaide/google-calendar"
log = logging.getLogger(__name__)


def connected() -> bool:
    s = runtime_settings()
    return bool(s.google_client_id and s.google_client_secret and s.google_refresh_token)


def fetch(session: Session, owner: Person, start: datetime, end: datetime, query: str = "") -> list[dict[str, Any]]:
    s = runtime_settings()
    token = google.access_token(s.google_client_id, s.google_client_secret, s.google_refresh_token)
    return google.list_events(token, start=start, end=end, query=query)


def enabled(session: Session) -> bool:
    """The owner installed the calendar skill and it's switched on."""
    return any(skill.manifest.id == SKILL_ID for skill in active(session))


def events_between(session: Session, owner: Person, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Events for proactive use (digest, meeting prep). Never raises: [] when the skill is off,
    Google isn't connected, or Google can't be reached."""
    try:
        if not enabled(session) or not connected():
            return []
        return fetch(session, owner, start, end)
    except Exception:  # a calendar hiccup must never break the digest
        log.warning("couldn't read the calendar for a briefing", exc_info=True)
        return []


class Unknown(Exception):
    """The calendar can't be asked right now (skill off, not connected, or Google unreachable)."""


def fresh_event(session: Session, owner: Person, event_id: str) -> dict[str, Any] | None:
    """The event as Google has it now, or None if it was deleted. Raises Unknown when we can't tell."""
    try:
        if not enabled(session) or not connected():
            raise Unknown("calendar not available")
        s = runtime_settings()
        token = google.access_token(s.google_client_id, s.google_client_secret, s.google_refresh_token)
        return google.get_event(token, event_id)
    except Unknown:
        raise
    except Exception as exc:
        log.warning("couldn't re-check a calendar event", exc_info=True)
        raise Unknown(str(exc)) from exc


def window(session: Session, owner: Person, start_words: str, days: int) -> tuple[datetime, datetime, Any]:
    tz = zone(owner_zone(session, owner))
    now = datetime.now(UTC).astimezone(tz)
    words = (start_words or "today").strip().lower()
    if words == "today":
        day = now.date()
    else:
        found = find_moment(words, now)
        day = found[0].astimezone(tz).date() if found else now.date()
    start = datetime.combine(day, time.min, tz)
    return start, start + timedelta(days=max(1, min(days, 14))), tz


def calendar_events(session: Session, owner: Person, args: dict[str, Any]) -> tuple[Any, bool]:
    if not connected():
        return "Google Calendar isn't connected yet. Ask the owner to connect it under Connections.", True
    start, end, tz = window(session, owner, str(args.get("from", "today")), int(args.get("days") or 1))
    try:
        events = fetch(session, owner, start, end, str(args.get("query") or ""))
    except google.GoogleError as exc:
        return google.tool_text(exc), True
    if not events:
        return f"No events between {start:%a %d %b} and {end - timedelta(seconds=1):%a %d %b}.", False
    bus.publish(CalendarEventsRead(session, events))  # knowledge.connector_facts: a few short facts, if turned on
    out = []
    for e in events:
        title, line = google.event_text(e, tz)
        out.append(
            {
                "type": "search_result",
                "source": f"ava://cal/{e.get('id', '')}",
                "title": f"Calendar · {title}",
                "content": [{"type": "text", "text": line}],
                "citations": {"enabled": True},
            }
        )
    return out, False


# --- free/busy and calendar actions -------------------------------------------------------------------

DAY_START, DAY_END = time(8), time(20)  # the hours free_busy describes as free or busy
OPTION = "actions"


def actions_on(session: Session) -> bool:
    """The owner switched on calendar actions (and agreed to what they add) for the instance in scope."""
    from superaide.skills.registry import option_on

    return option_on(session, SKILL_ID, OPTION)


def can_write(session: Session) -> bool:
    """Google granted calendar.events to the stored sign-in."""
    from superaide.vault.store import find

    row = find(session, "google", "refresh_token")
    return row is not None and google.EVENTS_SCOPE in ((row.meta or {}).get("scopes") or [])


def _token() -> str:
    s = runtime_settings()
    return google.access_token(s.google_client_id, s.google_client_secret, s.google_refresh_token)


def calendar_free_busy(session: Session, owner: Person, args: dict[str, Any]) -> tuple[Any, bool]:
    if not connected():
        return "Google Calendar isn't connected yet. Ask the owner to connect it under Connections.", True
    start, end, tz = window(session, owner, str(args.get("from", "today")), int(args.get("days") or 1))
    try:
        busy = google.free_busy(_token(), start=start, end=end)
    except google.GoogleError as exc:
        return google.tool_text(exc), True
    lines = []
    day = start
    while day < end:
        open_, close = datetime.combine(day.date(), DAY_START, tz), datetime.combine(day.date(), DAY_END, tz)
        blocks = [(max(b, open_), min(e, close)) for b, e in busy if e > open_ and b < close]
        free, cursor = [], open_
        for b, e in blocks:
            if b > cursor:
                free.append((cursor, b))
            cursor = max(cursor, e)
        if cursor < close:
            free.append((cursor, close))

        def span(pairs: list[tuple[datetime, datetime]]) -> str:
            return ", ".join(f"{a.astimezone(tz):%H:%M}–{b.astimezone(tz):%H:%M}" for a, b in pairs) or "none"

        lines.append(f"{day:%a %d %b}: busy {span(blocks)}; free {span(free)}")
        day += timedelta(days=1)
    return f"Free and busy, {DAY_START:%H:%M}–{DAY_END:%H:%M} ({tz}):\n" + "\n".join(lines), False


def _others(event: dict[str, Any]) -> list[str]:
    """Other people invited to an event (not the owner, not rooms or resources)."""
    return [
        str(a.get("email"))
        for a in event.get("attendees") or []
        if not a.get("self") and not a.get("resource") and a.get("email")
    ]


def _needs_approval(
    session: Session,
    effect: str,
    tainted: bool,
    *,
    tool: str = "",
    summary: str = "",
    args: dict[str, Any] | None = None,
) -> bool:
    """ADR 0006: side effects wait for the owner at the Infant and Child stages, and always after
    untrusted (web) content entered the turn.

    MANDATE_SKIP_ENABLED (#112): an event nobody else is told about is then a write_external tool, so it asks like
    one, and a covering signed mandate (Child and above, untainted, after enough shadow data) lets it run."""
    from superaide.mandate import service as mandate
    from superaide.skills.manifest import SIDE_EFFECTS
    from superaide.triage.store import current_stage
    from superaide.triage.types import Stage

    if tainted:
        return True
    if tool and effect == "write_own" and mandate.skip_enabled():
        if current_stage(session) not in (Stage.INFANT, Stage.CHILD):
            return False
        return not mandate.skips_card(
            session, tool=tool, args=args or {}, tainted=tainted, notifies=False, summary=summary
        )
    return effect in SIDE_EFFECTS and current_stage(session) in (Stage.INFANT, Stage.CHILD)


def _when(session: Session, owner: Person, words: str) -> tuple[datetime, Any] | str:
    tz = zone(owner_zone(session, owner))
    found = find_moment(words, datetime.now(UTC).astimezone(tz))
    if found is None:
        return f"Couldn't read the time {words!r}. Ask the owner when, e.g. 'tomorrow at 3pm'."
    return found[0].astimezone(tz), tz


def _clean_emails(raw: Any) -> list[str] | str:
    from email_validator import EmailNotValidError, validate_email

    out = []
    for item in raw or []:
        try:
            out.append(validate_email(str(item).strip(), check_deliverability=False).normalized.lower())
        except EmailNotValidError:
            return f"{str(item)[:80]!r} isn't an email address. Ask the owner for the address."
    return sorted(set(out))


def _ask(
    session: Session, sender: Any, kind: str, summary: str, payload: dict[str, Any], tainted: bool = False
) -> tuple[str, bool]:
    decision = decide(
        session, kind=kind, summary=summary, requester=sender, rules=TOOL_RULES, payload=payload, tainted=tainted
    )
    if decision.outcome == "approval":
        return (
            f"Waiting for the owner's approval: {summary}. Tell them it's waiting for their OK; it isn't done yet.",
            False,
        )
    return f"Refused: {decision.reason}.", True


def _guard(session: Session, sender: Any) -> str | None:
    if sender.relation != "manager":
        return "Only the owner can change their calendar."
    if not connected():
        return "Google Calendar isn't connected yet. Ask the owner to connect it under Connections."
    if not actions_on(session):
        return "Calendar actions are off. The owner can allow them under Capabilities → Google Calendar."
    if not can_write(session):
        return ("Google hasn't given Ava permission to change the calendar yet. Ask the owner to finish "
                "switching on calendar actions under Capabilities.")  # fmt: skip
    return None


def create_event(session: Session, sender: Any, args: dict[str, Any], *, tainted: bool) -> tuple[str, bool]:
    if problem := _guard(session, sender):
        return problem, True
    owner = sender.person
    when = _when(session, owner, str(args.get("when", "")))
    if isinstance(when, str):
        return when, True
    start, tz = when
    minutes = max(5, min(int(args.get("minutes") or 30), 1440))
    attendees = _clean_emails(args.get("attendees"))
    if isinstance(attendees, str):
        return attendees, True
    title = " ".join(str(args.get("title") or "").split())[:200] or "Event"
    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start.isoformat(), "timeZone": str(tz)},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(), "timeZone": str(tz)},
    }
    if args.get("location"):
        body["location"] = str(args["location"])[:300]
    if args.get("description"):
        body["description"] = str(args["description"])[:2000]
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    effect = "send" if attendees else "write_own"
    when_text = f"{start:%a %d %b, %H:%M}–{start + timedelta(minutes=minutes):%H:%M}"
    summary = f"Add “{title}” to your calendar, {when_text}" + (
        f", inviting {', '.join(attendees)}" if attendees else ""
    )
    if _needs_approval(session, effect, tainted, tool="create_event", summary=summary, args={"recipients": attendees}):
        return _ask(
            session, sender, "calendar_create_event", summary, {"event": body, "notify": bool(attendees)}, tainted
        )
    try:
        made = google.insert_event(_token(), body, notify=False)
    except google.GoogleError as exc:
        return google.tool_text(exc), True
    audit(session, "skills", "calendar_event_created", event_id=made.get("id"), by="tool")
    return f"Added “{title}”, {when_text} (event id {made.get('id', '')}).", False


def _existing(event_id: str) -> dict[str, Any] | str:
    if not event_id or len(event_id) > 1024:
        return "Give the event's id from calendar_events (the part after ava://cal/)."
    try:
        found = google.get_event(_token(), event_id)
    except google.GoogleError as exc:
        return google.tool_text(exc)
    if found is None or found.get("status") == "cancelled":
        return "That event isn't in the calendar any more."
    return found


def move_event(session: Session, sender: Any, args: dict[str, Any], *, tainted: bool) -> tuple[str, bool]:
    if problem := _guard(session, sender):
        return problem, True
    event = _existing(str(args.get("id") or ""))
    if isinstance(event, str):
        return event, True
    when = _when(session, sender.person, str(args.get("when", "")))
    if isinstance(when, str):
        return when, True
    start, tz = when
    old_start = (event.get("start") or {}).get("dateTime")
    old_end = (event.get("end") or {}).get("dateTime")
    if args.get("minutes"):
        length = timedelta(minutes=max(5, min(int(args["minutes"]), 1440)))
    elif old_start and old_end:
        length = datetime.fromisoformat(old_end) - datetime.fromisoformat(old_start)
    else:
        length = timedelta(minutes=30)
    body = {
        "start": {"dateTime": start.isoformat(), "timeZone": str(tz)},
        "end": {"dateTime": (start + length).isoformat(), "timeZone": str(tz)},
    }
    others = _others(event)
    title = str(event.get("summary") or "(no title)")[:200]
    summary = f"Move “{title}” to {start:%a %d %b, %H:%M}–{start + length:%H:%M}" + (
        f" (Google will tell {', '.join(others[:6])})" if others else ""
    )
    if _needs_approval(
        session,
        "send" if others else "write_own",
        tainted,
        tool="move_event",
        summary=summary,
        args={"recipients": others},
    ):
        payload = {"event_id": event["id"], "patch": body, "notify": bool(others)}
        return _ask(session, sender, "calendar_move_event", summary, payload, tainted)
    try:
        google.patch_event(_token(), str(event["id"]), body, notify=False)
    except google.GoogleError as exc:
        return google.tool_text(exc), True
    audit(session, "skills", "calendar_event_moved", event_id=event["id"], by="tool")
    return f"Moved “{title}” to {start:%a %d %b, %H:%M}.", False


def decline_event(session: Session, sender: Any, args: dict[str, Any], *, tainted: bool) -> tuple[str, bool]:
    if problem := _guard(session, sender):
        return problem, True
    event = _existing(str(args.get("id") or ""))
    if isinstance(event, str):
        return event, True
    if not any(a.get("self") for a in event.get("attendees") or []):
        return "The owner isn't a guest on that event (it's their own), so there's nothing to decline.", True
    title = str(event.get("summary") or "(no title)")[:200]
    note = " ".join(str(args.get("note") or "").split())[:300]
    summary = f"Decline “{title}”" + (f" with the note “{note}”" if note else "") + " (Google tells the organiser)"
    # Always the owner's call: declining sends a reply to the organiser (effect "send").
    return _ask(session, sender, "calendar_decline_event", summary, {"event_id": event["id"], "note": note}, tainted)


TOOLS = {"calendar_free_busy", "create_event", "move_event", "decline_event"}


def run_tool(session: Session, sender: Any, name: str, args: dict[str, Any], *, tainted: bool) -> tuple[Any, bool]:
    if name == "calendar_free_busy":
        return calendar_free_busy(session, sender.person, args)
    if name == "create_event":
        return create_event(session, sender, args, tainted=tainted)
    if name == "move_event":
        return move_event(session, sender, args, tainted=tainted)
    return decline_event(session, sender, args, tainted=tainted)


# --- what approving does ----------------------------------------------------------------------------


def _carry_out(session: Session, approval: Any, fn: Any, done: str) -> str:
    try:
        if not connected() or not can_write(session):
            return "Approved, but Google Calendar isn't connected with calendar actions any more. Nothing changed."
        result = fn(_token(), dict(approval.payload or {}))
    except google.GoogleError as exc:
        return f"Approved, but Google said: {google.tool_text(exc)} Nothing changed."
    audit(session, "skills", f"calendar_{approval.kind.removeprefix('calendar_')}", approval_id=approval.id,
          event_id=(result or {}).get("id"))  # fmt: skip
    return done


def _do_create(token: str, p: dict[str, Any]) -> dict[str, Any]:
    return google.insert_event(token, dict(p["event"]), notify=bool(p.get("notify")))


def _do_move(token: str, p: dict[str, Any]) -> dict[str, Any]:
    return google.patch_event(token, str(p["event_id"]), dict(p["patch"]), notify=bool(p.get("notify")))


def _do_decline(token: str, p: dict[str, Any]) -> dict[str, Any]:
    event = google.get_event(token, str(p["event_id"]))
    if event is None:
        raise google.GoogleError("That event isn't in the calendar any more.")
    attendees = [dict(a) for a in event.get("attendees") or []]
    for a in attendees:
        if a.get("self"):
            a["responseStatus"] = "declined"
            if p.get("note"):
                a["comment"] = str(p["note"])
    return google.patch_event(token, str(p["event_id"]), {"attendees": attendees}, notify=True)


def _register() -> None:
    EXECUTORS["calendar_create_event"] = lambda s, a: _carry_out(s, a, _do_create, "Added to your calendar.")
    EXECUTORS["calendar_move_event"] = lambda s, a: _carry_out(s, a, _do_move, "Moved.")
    EXECUTORS["calendar_decline_event"] = lambda s, a: _carry_out(
        s, a, _do_decline, "Declined; the organiser was told."
    )


_register()
