# ruff: noqa: E501
"""Time words in a question, read in code in the owner's time zone ("last week", "yesterday", "in October").

`find_span(text, now, tz)` returns the range the question names and the text without those words, so "what did Meera
say last week about the invoice?" searches for the invoice, not for the words "last" and "week". The range is
half-open (`since <= t < until`), either end may be None ("since Monday" has no end), and a week runs Monday to Sunday:
"last week" is the previous calendar week, also when it is asked on a Monday.

The model never decides what a time word means; it can only pass `since` and `until` itself (ISO dates), which are
read here too (`parse_date`). Only the first time phrase is read; the others stay in the text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
            ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
            ("november", "nov"), ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}  # fmt: skip
WEEKDAYS = {
    n: i
    for i, names in enumerate(
        [("monday", "mon"), ("tuesday", "tue", "tues"), ("wednesday", "wed"), ("thursday", "thu", "thur", "thurs"),
         ("friday", "fri"), ("saturday", "sat"), ("sunday", "sun")]
    )
    for n in names
}  # fmt: skip
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_UNIT = r"(?:hour|day|week|month)s?"


@dataclass(frozen=True)
class Span:
    since: datetime | None  # UTC, inclusive
    until: datetime | None  # UTC, exclusive
    label: str  # for the owner: "last week", "in October"
    relative: bool  # "yesterday" is relative to now; "in October" names a month

    def describe(self, tz: str) -> str:
        """ "between 9 and 15 November", "on 19 November", "since 2 November": the range in the owner's zone."""
        zone = ZoneInfo(tz)
        first = self.since.astimezone(zone).date() if self.since else None
        last = (self.until.astimezone(zone) - timedelta(microseconds=1)).date() if self.until else None
        if first and last:
            if first == last:
                return f"on {_d(first, True)}"
            if (first.month, first.year) == (last.month, last.year):
                return f"between {first.day} and {_d(last, True)}"
            return f"between {_d(first, True)} and {_d(last, True)}"
        if first:
            return f"since {_d(first, True)}"
        if last:
            return f"up to {_d(last, True)}"
        return ""


def _d(day: date, with_month: bool = False) -> str:
    return f"{day.day} {day:%B}" if with_month else str(day.day)


def _at(day: date, zone: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def _recent_month(month: int, today: date) -> int:
    """The year of the latest `month` that has begun: this year, or last year for a month still to come."""
    return today.year if month <= today.month else today.year - 1


def _weekday_before(target: int, today: date, *, include_today: bool) -> date:
    back = (today.weekday() - target) % 7
    return today - timedelta(days=back if (back or include_today) else 7)


_Found = tuple[datetime | None, datetime | None, str, bool]
Handler = Callable[[re.Match[str], date, datetime, ZoneInfo], _Found]


def _day_span(day: date, zone: ZoneInfo, label: str, relative: bool) -> _Found:
    return _at(day, zone), _at(day + timedelta(days=1), zone), label, relative


def _yesterday(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    return _day_span(today - timedelta(days=1), zone, "yesterday", True)


def _today(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    return _day_span(today, zone, "today", True)


def _this_morning(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    return _at(today, zone), _at(today, zone) + timedelta(hours=12), "this morning", True


def _last_night(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    start = _at(today - timedelta(days=1), zone) + timedelta(hours=18)
    return start, _at(today, zone) + timedelta(hours=6), "last night", True


def _week(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    this = _monday(today)
    first = this - timedelta(days=7) if m.group("which").lower() == "last" else this
    return _at(first, zone), _at(first + timedelta(days=7), zone), f"{m.group('which').lower()} week", True


def _month(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    first = _month_start(today.year, today.month)
    if m.group("which").lower() == "last":
        first = _month_start(first.year - (first.month == 1), (first.month - 2) % 12 + 1)
    return _at(first, zone), _at(_next_month(first), zone), f"{m.group('which').lower()} month", True


def _year(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    year = today.year - (m.group("which").lower() == "last")
    return _at(date(year, 1, 1), zone), _at(date(year + 1, 1, 1), zone), f"{m.group('which').lower()} year", True


_PER = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1), "month": timedelta(days=30)}


def _rolling(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    """ "in the last 3 days", "past week": the stretch up to now."""
    n = int(m.groupdict().get("n") or 1)
    unit = m.group("unit").lower().rstrip("s")
    return now.astimezone(UTC) - _PER[unit] * n, None, m.group(0).strip().lower(), True


def _ago(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    n, unit = int(m.group("n")), m.group("unit").lower().rstrip("s")
    if unit == "hour":
        return now - timedelta(hours=n, minutes=30), now - timedelta(hours=n - 1, minutes=30), m.group(0), True
    if unit == "day":
        return _day_span(today - timedelta(days=n), zone, m.group(0).lower(), True)
    if unit == "week":
        first = _monday(today) - timedelta(days=7 * n)
        return _at(first, zone), _at(first + timedelta(days=7), zone), m.group(0).lower(), True
    first = today.replace(day=1)
    for _ in range(n):
        first = _month_start(first.year - (first.month == 1), (first.month - 2) % 12 + 1)
    return _at(first, zone), _at(_next_month(first), zone), m.group(0).lower(), True


def _weekday(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    target = WEEKDAYS[m.group("day").lower()]
    last = (m.groupdict().get("prefix") or "").lower() == "last"
    return _day_span(_weekday_before(target, today, include_today=not last), zone, m.group(0).lower(), False)


def _in_month(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    month = MONTHS[m.group("month").lower()]
    year = int(m.group("year")) if m.groupdict().get("year") else _recent_month(month, today)
    first = _month_start(year, month)
    return _at(first, zone), _at(_next_month(first), zone), m.group(0).lower(), False


def _date(m: re.Match[str], today: date, now: datetime, zone: ZoneInfo) -> _Found:
    gd = m.groupdict()
    if gd.get("iso"):
        day = date.fromisoformat(gd["iso"])
    else:
        month = MONTHS[gd["month"].lower()]
        year = int(gd["year"]) if gd.get("year") else _recent_month(month, today)
        day = date(year, month, int(gd["d"]))
        if not gd.get("year") and day > today:
            day = date(year - 1, month, int(gd["d"]))
    return _day_span(day, zone, m.group(0).lower(), False)


_PREP = r"(?:\b(?:in|on|during|from|of)\s+)?"
_SIMPLE: list[tuple[re.Pattern[str], Handler]] = [
    (re.compile(_PREP + r"\byesterday\b", re.I), _yesterday),
    (re.compile(_PREP + r"\btoday\b", re.I), _today),
    (re.compile(r"\bthis\s+morning\b", re.I), _this_morning),
    (re.compile(r"\b(?:last|yesterday)\s+night\b", re.I), _last_night),
    (re.compile(_PREP + r"\b(?P<which>last|this)\s+week\b", re.I), _week),
    (re.compile(_PREP + r"\b(?P<which>last|this)\s+month\b", re.I), _month),
    (re.compile(_PREP + r"\b(?P<which>last|this)\s+year\b", re.I), _year),
    (re.compile(r"\b(?:in\s+the\s+)?(?:last|past)\s+(?P<n>\d+)\s+(?P<unit>" + _UNIT + r")\b", re.I), _rolling),
    (re.compile(r"\b(?:in\s+the\s+)?past\s+(?P<unit>week|month|day|hour)\b", re.I), _rolling),
    (re.compile(r"\b(?P<n>\d+)\s+(?P<unit>" + _UNIT + r")\s+ago\b", re.I), _ago),
    (re.compile(r"\b(?P<prefix>last)\s+(?P<day>" + _DAY + r")\b", re.I), _weekday),
    (re.compile(r"\bon\s+(?P<day>" + _DAY + r")\b", re.I), _weekday),
    (re.compile(r"\b(?P<iso>\d{4}-\d{2}-\d{2})\b"), _date),
    (re.compile(r"\b(?:on\s+)?(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<month>" + _MONTH + r")\b(?:\s+(?P<year>\d{4}))?", re.I), _date),
    (re.compile(r"\b(?:on\s+)?(?P<month>" + _MONTH + r")\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(?P<year>\d{4}))?", re.I), _date),
    # A bare month name is a word like any other ("may", "march"): only "in October" or "during October" is a time.
    (re.compile(r"\b(?:in|during)\s+(?P<month>" + _MONTH + r")\b(?:\s+(?P<year>\d{4}))?", re.I), _in_month),
]  # fmt: skip
_SINCE = re.compile(r"\bsince\s+", re.I)
_SINCE_DAY = re.compile(r"(?:last\s+)?(?P<day>" + _DAY + r")\b", re.I)


def _first(text: str) -> tuple[re.Match[str], Handler] | None:
    best: tuple[re.Match[str], Handler] | None = None
    for pattern, handler in _SIMPLE:
        m = pattern.search(text)
        if m and (
            best is None or m.start() < best[0].start() or (m.start() == best[0].start() and m.end() > best[0].end())
        ):
            best = (m, handler)
    return best


def find_span(text: str, now: datetime, tz: str) -> tuple[Span | None, str]:
    """(the range the question names, the question without those words). (None, text) when it names none."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    today = local.date()
    since = _SINCE.search(text)
    if since:  # "since Monday", "since last week", "since 2 November": an open end
        rest = text[since.end() :]
        weekday = _SINCE_DAY.match(rest)
        if weekday:  # the most recent such day, today included
            start = _at(_weekday_before(WEEKDAYS[weekday.group("day").lower()], today, include_today=True), zone)
            label = f"since {weekday.group('day').lower()}"
            return Span(start, None, label, False), _cut(text, since.start(), since.end() + weekday.end())
        inner = _first(rest)
        if inner and inner[0].start() == 0:
            begin, _end, _label, relative = inner[1](inner[0], today, local, zone)
            if begin is not None:
                label = "since " + inner[0].group(0).lower()
                return Span(begin, None, label, relative), _cut(text, since.start(), since.end() + inner[0].end())
    found = _first(text)
    if found is None:
        return None, text
    m, handler = found
    first, end, label, relative = handler(m, today, local, zone)
    return Span(first, end, label, relative), _cut(text, m.start(), m.end())


def _cut(text: str, start: int, end: int) -> str:
    return " ".join((text[:start] + " " + text[end:]).split())


def parse_date(value: str, tz: str, *, end: bool = False) -> datetime | None:
    """An ISO date (or datetime) the model passed as `since` or `until`, in the owner's zone. A bare date as `until`
    covers that whole day. None when it is not a date."""
    value = (value or "").strip()
    if not value:
        return None
    zone = ZoneInfo(tz)
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return _at(day + timedelta(days=1 if end else 0), zone)
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=zone)).astimezone(UTC)
