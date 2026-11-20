"""Choosing the few times a visitor is offered (#496). Pure code: no database, no network, no clock of its own.

Input is what code already read (busy intervals from Google's free/busy, the owner's working hours and zone, a
buffer, a lead time); output is at most three slots. The visitor, and the model, never see the busy list, the
free grid or the owner's zone: only the slots returned here, and only after the owner's settings allowed them.

Rules, all in code:
- Working days are Monday to Friday, between `day_start` and `day_end` in the owner's zone.
- A slot starts on a 30-minute boundary, never sooner than `lead` from now, never inside `blocked` (away, a status).
- A slot keeps `buffer` minutes clear of every busy interval and of every time already held for someone else.
- Daylight saving: wall-clock times that do not exist (the spring gap) are skipped; times are compared in UTC.
- At most `limit` slots, from different days where possible, so the result is a choice and never the grid.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from superaide.intentions.when import zone

Interval = tuple[datetime, datetime]
STEP = 30  # minutes: slots start on :00 and :30
PREFERRED = (time(10, 30), time(14, 0))  # alternate, so the offer is a morning and an afternoon, not 09:00 x3
SPACING = timedelta(hours=2)  # two slots on the same day are at least this far apart


def visitor_zone(name: str) -> str:
    """The IANA zone a visitor stated, if it is one; else UTC. Never guessed from anything else."""
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > 64 or cleaned.upper() == "UTC":
        return "UTC"
    try:
        ZoneInfo(cleaned)
    except ZoneInfoNotFoundError, ValueError, OSError:
        return "UTC"
    return cleaned


def _local(day: date, at: time, tz: ZoneInfo) -> datetime | None:
    """The wall-clock time on `day` in `tz`, or None when DST skips it (the spring-forward gap)."""
    dt = datetime.combine(day, at, tz)
    back = dt.astimezone(ZoneInfo("UTC")).astimezone(tz)
    return dt if back.replace(tzinfo=None) == dt.replace(tzinfo=None) else None


def _clear(start: datetime, end: datetime, taken: Sequence[Interval], margin: timedelta) -> bool:
    return all(end <= b - margin or start >= e + margin for b, e in taken)


def candidates(
    *,
    now: datetime,
    tz: ZoneInfo,
    minutes: int,
    day_start: int,
    day_end: int,
    taken: Sequence[Interval],
    buffer: int,
    lead: timedelta,
    days: int,
) -> dict[date, list[Interval]]:
    """Every free slot per owner-local day over the next `days` days. Internal: callers choose from it."""
    zone_ = tz
    margin = timedelta(minutes=buffer)
    length = timedelta(minutes=minutes)
    earliest = now + lead
    out: dict[date, list[Interval]] = {}
    first_day = now.astimezone(zone_).date()
    for n in range(days + 1):
        day = first_day + timedelta(days=n)
        if day.weekday() >= 5:
            continue
        wall = datetime.combine(day, time(day_start))
        closes = datetime.combine(day, time(day_end))
        while wall + length <= closes:
            start = _local(day, wall.time(), zone_)
            if start is not None:
                begin = start.astimezone(ZoneInfo("UTC"))
                end = begin + length  # real elapsed time, so a slot across a DST change is still `minutes` long
                if begin >= earliest and _clear(begin, end, taken, margin):
                    out.setdefault(day, []).append((begin, end))
            wall += timedelta(minutes=STEP)
    return out


def choose(found: dict[date, list[Interval]], tz: ZoneInfo, *, limit: int, want: date | None = None) -> list[Interval]:
    """Pick up to `limit` slots: two on the day the visitor named (if it has room), then one per later day."""
    picked: list[Interval] = []

    def nearest(options: list[Interval], target: time, day: date) -> Interval | None:
        ok = [o for o in options if all(abs(o[0] - p[0]) >= SPACING for p in picked)]
        if not ok:
            return None
        goal = datetime.combine(day, target)
        return min(ok, key=lambda o: abs(o[0].astimezone(tz).replace(tzinfo=None) - goal))

    order = sorted(found)
    if want is not None and want in found:
        order = [want] + [d for d in order if d != want]
    for round_no, day in enumerate(order):
        slots = found[day]
        for k in range(2 if day == want else 1):
            if len(picked) >= limit:
                break
            slot = nearest(slots, PREFERRED[(round_no + k) % 2], day)
            if slot is not None:
                picked.append(slot)
    if len(picked) < limit:  # fewer days than slots: a second slot on a day already used
        for day in order:
            slot = nearest([s for s in found[day] if s not in picked], PREFERRED[0], day)
            if slot is not None and len(picked) < limit:
                picked.append(slot)
    return sorted(picked)[:limit]


def render(slot: Interval, tz_name: str) -> str:
    """ "Thursday 12 November, 10:30" in the zone the visitor sees."""
    local = slot[0].astimezone(zone(tz_name))
    return f"{local:%A %d %B}, {local:%H:%M}"
