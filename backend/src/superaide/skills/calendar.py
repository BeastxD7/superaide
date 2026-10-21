"""The calendar_events tool: the owner's Google Calendar as citable sources."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from superaide.connectors import google
from superaide.db.models import Person
from superaide.intentions.service import owner_zone
from superaide.intentions.when import find_moment, zone
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
        return str(exc), True
    if not events:
        return f"No events between {start:%a %d %b} and {end - timedelta(seconds=1):%a %d %b}.", False
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
