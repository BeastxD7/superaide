"""Edge cases in reading "in N units": absurd numbers and clock changes (issue #92, BE-10 and BE-11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from superaide.intentions.when import find_moment, parse_reminder, zone

NY = zone("America/New_York")


@pytest.mark.parametrize(
    "said",
    [
        "remind me in 99999999 days to renew",
        "remind me in 5000000 weeks to renew",
        "remind me in 999999999999 hours to renew",
        "remind me in 99999999999999999999 minutes to renew",
        "remind me in " + "9" * 5000 + " days to renew",
        "ship it in 5000000 days lol",
        "remind me in 4000 days to renew",
    ],
    ids=["days", "weeks", "hours", "minutes", "5000-digit-number", "ship-it", "over-ten-years"],
)
def test_an_absurdly_far_moment_is_no_moment_rather_than_an_error(said):
    now = datetime(2026, 10, 21, 23, 0, tzinfo=zone("Asia/Singapore"))
    assert find_moment(said, now) is None
    assert parse_reminder(said, now) is None


def test_a_few_years_ahead_is_still_fine():
    now = datetime(2026, 10, 21, 23, 0, tzinfo=zone("Asia/Singapore"))
    found = find_moment("in 100 weeks", now)
    assert found is not None and found[0] == now + timedelta(weeks=100)


def test_in_three_hours_is_three_real_hours_when_the_clocks_fall_back():
    now = datetime(2026, 11, 1, 0, 30, tzinfo=NY)  # EDT; the clocks fall back at 02:00
    found = find_moment("in 3 hours", now)
    assert found is not None
    assert found[0].astimezone(UTC) - now.astimezone(UTC) == timedelta(hours=3)
    assert found[0].tzinfo is NY or str(found[0].tzinfo) == "America/New_York"


def test_in_one_hour_is_one_real_hour_when_the_clocks_spring_forward():
    now = datetime(2026, 3, 8, 1, 30, tzinfo=NY)  # EST; 02:00 jumps to 03:00
    found = find_moment("in 1 hour", now)
    assert found is not None
    assert found[0].astimezone(UTC) - now.astimezone(UTC) == timedelta(hours=1)


def test_in_twenty_minutes_is_twenty_real_minutes_across_a_clock_change():
    now = datetime(2026, 3, 8, 1, 50, tzinfo=NY)
    found = find_moment("in 20 minutes", now)
    assert found is not None
    assert found[0].astimezone(UTC) - now.astimezone(UTC) == timedelta(minutes=20)


def test_in_days_keeps_the_wall_clock_time_across_a_clock_change():
    now = datetime(2026, 10, 31, 9, 0, tzinfo=NY)  # EDT; the clocks fall back the next night
    found = find_moment("in 2 days", now)
    assert found is not None
    assert (found[0].hour, found[0].minute) == (9, 0)
