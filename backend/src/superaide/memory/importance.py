"""How much a fact matters, whether it fades, and how it ranks in recall (#49).

Decided in code from the category and the wording; the extraction model may suggest a value, which
is clamped here. Only **time-bound** facts (availability, plans, events) decay, so a CEO's role never
fades because nobody asked about it this month, while "I'm free Friday" does.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

IMPORTANCE = {"sharing": 0.9, "about": 0.8, "work": 0.8, "person": 0.8, "preference": 0.7, "urgency": 0.6}
DEFAULT_IMPORTANCE = 0.4  # availability and other
TIME_BOUND_CATEGORIES = {"availability", "urgency"}
TIME_WORDS = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|this (?:week|weekend|month|morning|afternoon|evening)|next (?:week|month)|"
    r"on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|until|unavailable|travelling|traveling|"
    r"out of office|on leave)\b",
    re.I,
)

HALF_LIFE_DAYS = 14.0  # a time-bound fact counts half as much after this long
DECAY_FLOOR = 0.05
RECHECK_MIN_IMPORTANCE = 0.7
RECHECK_INTERVAL_DAYS = (30, 90, 180, 365)  # expanding: after the 1st, 2nd, ... confirmation


def is_time_bound(category: str, content: str) -> bool:
    return category in TIME_BOUND_CATEGORIES or TIME_WORDS.search(content) is not None


def score(category: str, suggested: float | None = None) -> float:
    base = IMPORTANCE.get(category, DEFAULT_IMPORTANCE)
    if suggested is None:
        return base
    return round(max(0.0, min(1.0, 0.5 * base + 0.5 * suggested)), 2)  # code keeps half the say


def decay(time_bound: bool, since: datetime, now: datetime | None = None) -> float:
    """1.0 for a fact that doesn't fade; for a time-bound one, 0.5 ** (age / half-life), floored."""
    if not time_bound:
        return 1.0
    age = ((now or datetime.now(UTC)) - since).total_seconds() / 86400
    return max(DECAY_FLOOR, 0.5 ** (max(0.0, age) / HALF_LIFE_DAYS))


def weight(row: Any, now: datetime | None = None) -> float:
    """The multiplier recall puts on a row's fused score: importance (0.5 is neutral) times decay.
    Rows without these columns (messages) weigh 1."""
    importance = getattr(row, "importance", None)
    if importance is None:
        return 1.0
    since = getattr(row, "last_verified_at", None) or row.created_at
    return (0.5 + float(importance)) * decay(bool(getattr(row, "time_bound", False)), since, now)
