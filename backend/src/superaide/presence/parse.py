"""Read an owner's sentence about being unavailable ("busy until 3 pm", "driving until 5, only family gets through").

Pure code, no model: a kind from fixed words, an end time (required: a state without an end would never expire), and
who still gets through. Returns None when the sentence is not about presence or has no end, so the caller asks
"until when?" instead of guessing."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo

MAX_SPAN = timedelta(days=14)
RELATIONS = ("family", "partner", "boss", "team", "vip")

_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("driving", re.compile(r"\b(?:driv(?:e|ing)|on the road|commut(?:e|ing))\b", re.I)),
    ("focus", re.compile(r"\b(?:focus(?:ing)?|heads?[- ]down|deep work)\b", re.I)),
    ("travel", re.compile(r"\b(?:travell?ing|flying|on a (?:flight|plane|train))\b", re.I)),
    ("away", re.compile(r"\b(?:away|out of office|ooo|on leave|on holiday|on vacation)\b", re.I)),
    (
        "busy",
        re.compile(r"\b(?:busy|in (?:a )?(?:meeting|meetings|call)|do not disturb|dnd|unavailable|offline)\b", re.I),
    ),
)
_CLOCK = re.compile(
    r"\b(?:until|till|til|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\d:])(?!\s*(?:months?|weeks?|days?|hours?|hrs?|minutes?|mins?|years?)\b)",
    re.I,
)
_EXCEPT = re.compile(r"\b(?:except|only|unless|but)\b", re.I)


@dataclass(frozen=True)
class Parsed:
    kind: str
    until: datetime
    allow_relations: list[str] = field(default_factory=list)


def _clock(text: str, now: datetime) -> datetime | None:
    """ "until 3 pm", "till 4:30", "until 5": the next time that clock reads, in `now`'s zone. A bare hour 1-12 is
    the next one ahead (4:30 at 10:00 is this afternoon; 9 at 22:00 is tomorrow morning)."""
    m = _CLOCK.search(text)
    if m is None:
        return None
    hour, minute, mer = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
    if minute > 59 or hour > 24 or (mer and not 1 <= hour <= 12):
        return None
    hours = [hour % 24]
    if mer:
        hours = [hour % 12 + (12 if mer == "pm" else 0)]
    elif 1 <= hour <= 12:
        hours = [hour % 12, hour % 12 + 12]
    candidates = [now.replace(hour=h, minute=minute, second=0, microsecond=0) for h in hours]
    candidates += [c + timedelta(days=1) for c in candidates]
    ahead = sorted(c for c in candidates if c > now)
    return ahead[0] if ahead else None


def parse(
    text: str,
    now: datetime,
    tz: tzinfo,
    find_moment: Callable[[str, datetime], tuple[datetime, str] | None] | None = None,
) -> Parsed | None:
    """`find_moment` is the app's date reader ("for 2 hours", "tomorrow 9am"), passed in by the caller; without it
    only clock times ("until 3 pm") are read."""
    kind = next((k for k, rx in _KINDS if rx.search(text)), None)
    if kind is None:
        return None
    local = now.astimezone(tz)
    until = _clock(text, local)
    if until is None and find_moment is not None:
        found = find_moment(text, local)
        until = found[0] if found else None
    if until is None or until <= local or until - local > MAX_SPAN:
        return None
    allow: list[str] = []
    if _EXCEPT.search(text):
        allow = [r for r in RELATIONS if re.search(rf"\b{r}\b", text, re.I)]
    return Parsed(kind=kind, until=until.astimezone(now.tzinfo), allow_relations=allow)
