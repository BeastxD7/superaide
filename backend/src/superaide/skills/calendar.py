"""The calendar_events tool: the owner's Google Calendar as citable sources."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from superaide.connectors import google
from superaide.db.models import Person
from superaide.intentions.service import owner_zone
from superaide.intentions.when import find_moment, zone
from superaide.vault.store import runtime_settings


def connected() -> bool:
    s = runtime_settings()
    return bool(s.google_client_id and s.google_client_secret and s.google_refresh_token)


def fetch(session: Session, owner: Person, start: datetime, end: datetime, query: str = "") -> list[dict[str, Any]]:
    s = runtime_settings()
    token = google.access_token(s.google_client_id, s.google_client_secret, s.google_refresh_token)
    return google.list_events(token, start=start, end=end, query=query)


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
