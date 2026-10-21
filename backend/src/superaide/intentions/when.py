"""Turn "remind me to call Priya on Friday at 5" into a task and a moment.

Handled directly: "in 20 minutes", "in 2 hours", "today/tonight/tomorrow", weekdays
("Friday", "next Monday"), times ("at 5", "5pm", "5:30 pm", "17:00", "noon", "morning").
Anything else (for example "24 Oct 3pm") falls back to dateparser. All times are in
the owner's time zone; a day without a time means 9 am, and a time that has already
passed today means tomorrow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PARTS_OF_DAY = {"morning": (9, 0), "noon": (12, 0), "midday": (12, 0), "afternoon": (15, 0), "evening": (18, 0)}
DEFAULT_TIME = (9, 0)

REMIND_RE = re.compile(
    r"^\s*(?:please\s+)?remind\s+me\s+(?:to\s+|about\s+|that\s+)?(?P<rest>.+?)\s*[.!]?\s*$", re.I | re.S
)
FOLLOW_UP_RE = re.compile(
    r"^\s*(?:please\s+)?(?:remind\s+me\s+to\s+)?follow\s+up\s+with\s+(?P<rest>.+?)\s*[.!]?\s*$", re.I | re.S
)

_IN = re.compile(
    r"\bin\s+(?P<n>\d+|an?|one|two|three|half\s+an?)\s*(?P<unit>minutes?|mins?|hours?|hrs?|days?|weeks?)\b", re.I
)
_TIME = re.compile(r"\b(?:at\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)?(?=\s|$|[.,!?])", re.I)
_DAY = re.compile(
    r"\b(?:(?:on|this|next|coming)\s+)*(?P<day>today|tonight|tomorrow|tmrw|" + "|".join(WEEKDAYS) + r")\b",
    re.I,
)
_PART = re.compile(r"\b(?:in\s+the\s+|this\s+)?(?P<part>morning|noon|midday|afternoon|evening)\b", re.I)
_DATE = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    re.I,
)
_WORD_N = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}


@dataclass
class Parsed:
    task: str
    due: datetime  # timezone-aware, in the owner's zone


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo("UTC")


def clean_task(task: str) -> str:
    task = re.sub(r"\s+", " ", task).strip(" ,.;:-")
    task = re.sub(r"^(?:to|about|that)\s+", "", task, flags=re.I)
    task = re.sub(r"\s+(?:on|at|by|this|next|in|the)$", "", task, flags=re.I).strip(" ,.;:-")
    return task


def when(text: str, now: datetime) -> tuple[datetime, str] | None:
    """Find a moment in `text` (relative to `now`, which carries the owner's zone).
    Returns the moment and the text with the time words removed, or None."""
    rest = text
    m = _IN.search(rest)
    if m:
        raw = m.group("n").lower()
        n = 0.5 if raw.startswith("half") else _WORD_N.get(raw) or int(raw)
        unit = m.group("unit").lower()
        delta = (
            timedelta(minutes=n)
            if unit.startswith("min")
            else timedelta(hours=n)
            if unit.startswith(("hour", "hr"))
            else timedelta(days=n)
            if unit.startswith("day")
            else timedelta(weeks=n)
        )
        return now + delta, rest[: m.start()] + rest[m.end() :]

    day: datetime | None = None
    hm: tuple[int, int] | None = None
    dm = _DAY.search(rest)
    if dm:
        word = dm.group("day").lower()
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if word in ("today", "tonight"):
            day = base
            if word == "tonight":
                hm = (20, 0)
        elif word in ("tomorrow", "tmrw"):
            # Just after midnight, "tomorrow" means after you've slept: today's date.
            day = base if now.hour < 4 else base + timedelta(days=1)
        else:
            target = WEEKDAYS.index(word)
            ahead = (target - now.weekday()) % 7 or 7  # "Friday" on a Friday means next week
            if "next" in dm.group(0).lower() and ahead < 7 and (target - now.weekday()) % 7 != 0:
                ahead = ahead if ahead > (6 - now.weekday()) else ahead + 7
            day = base + timedelta(days=ahead)
        rest = rest[: dm.start()] + rest[dm.end() :]

    tm = None
    for cand in _TIME.finditer(rest):
        h = int(cand.group("h"))
        has_marker = cand.group("ap") or cand.group("m") or cand.group(0).lower().startswith("at")
        if has_marker and 0 <= h <= 23:
            tm = cand
            break
    if tm:
        h, minute = int(tm.group("h")), int(tm.group("m") or 0)
        ap = (tm.group("ap") or "").lower().replace(".", "")
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        elif not ap and h <= 7 and not tm.group("m"):
            h += 12  # "at 5" means 5 pm; nobody sets a reminder for 5 am that way
        if minute < 60:
            hm = (h, minute)
            rest = rest[: tm.start()] + rest[tm.end() :]
    else:
        pm = _PART.search(rest)
        if pm:
            hm = PARTS_OF_DAY[pm.group("part").lower()]
            rest = rest[: pm.start()] + rest[pm.end() :]

    if day is None and hm is None:
        return None
    hour, minute = hm or DEFAULT_TIME
    if day is None:
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
    else:
        due = day.replace(hour=hour, minute=minute)
        if due <= now and day.date() == now.date():
            due += timedelta(days=1)
    return due, rest


def _fallback(text: str, now: datetime) -> tuple[datetime, str] | None:
    import dateparser.search

    found = dateparser.search.search_dates(
        text,
        languages=["en"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now.replace(tzinfo=None),
            "TIMEZONE": str(now.tzinfo),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if not found:
        return None
    phrase, moment = found[-1]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=now.tzinfo)
    if moment.hour == 0 and moment.minute == 0 and not re.search(r"\d\s*(?:am|pm|:)", phrase, re.I):
        moment = moment.replace(hour=DEFAULT_TIME[0], minute=DEFAULT_TIME[1])
    if moment <= now:
        return None
    return moment.astimezone(now.tzinfo), text.replace(phrase, " ")


def find_moment(text: str, now: datetime) -> tuple[datetime, str] | None:
    """The moment in `text` and the text without it; one reader for chat, tools and the dashboard.
    An explicit calendar date ("24 Oct", "3/11") is better read by dateparser."""
    return (_fallback(text, now) if _DATE.search(text) else None) or when(text, now) or _fallback(text, now)


def parse_reminder(text: str, now: datetime) -> Parsed | None:
    """A reminder request ("remind me to …", "follow up with …"), or None if this isn't one
    or no moment could be found (the caller then asks when)."""
    follow = FOLLOW_UP_RE.match(text)
    remind = REMIND_RE.match(text)
    if follow:
        body, prefix = follow.group("rest"), "Follow up with "
    elif remind:
        body, prefix = remind.group("rest"), ""
    else:
        return None
    found = find_moment(body, now)
    if found is None:
        return None
    due, rest = found
    task = clean_task(rest)
    if not task:
        return None
    task = prefix + task if prefix else task[:1].upper() + task[1:]
    return Parsed(task=task, due=due)


def is_reminder_request(text: str) -> bool:
    return bool(REMIND_RE.match(text) or FOLLOW_UP_RE.match(text))
