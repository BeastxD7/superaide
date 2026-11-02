# ruff: noqa: F811
"""A stranger's deadline keeps its time of day all the way to ranking and the inbox (#490)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.memory.knowledge.store import preferences_for
from superaide.platform.db.session import session_scope
from superaide.reception import service
from superaide.reception.models import ReceptionMessage
from superaide.reception.turn import ReceptionTurn, TakenMessage
from tests.reception.test_reception import (  # noqa: F401  (fixtures re-export)
    Receptionist,
    ava,  # noqa: F401
    connected,
    hello,
    outbox,
    owner_world,  # noqa: F401  (used through pytestmark)
    script,  # noqa: F401
    set_reception,
    taken,
)

pytestmark = pytest.mark.usefixtures("owner_world")  # a signed-up owner (Kolkata) who hired Ava

# 2026-11-03 is a Tuesday. In Kolkata (UTC+5:30) the moment below is 10:00 on that day.
ARRIVED = datetime(2026, 11, 3, 4, 30, tzinfo=UTC)


def read(text: str, tz: str = "Asia/Kolkata", arrived: datetime = ARRIVED):
    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        preferences_for(s, owner).timezone = tz
        return service._deadline(s, owner, text, arrived)


@pytest.mark.parametrize(
    ("text", "tz", "expected"),
    [
        ("within 5 minutes", "Asia/Kolkata", ARRIVED + timedelta(minutes=5)),
        ("in 2 hours", "Asia/Kolkata", ARRIVED + timedelta(hours=2)),
        ("by 3pm", "Asia/Kolkata", datetime(2026, 11, 3, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))),
        # the owner's zone decides what "3pm" is
        ("by 3pm", "America/New_York", datetime(2026, 11, 3, 15, 0, tzinfo=ZoneInfo("America/New_York"))),
    ],
)
def test_a_deadline_keeps_its_time_of_day(text, tz, expected):
    if tz == "America/New_York":  # 04:30 UTC is 23:30 the evening before there, so "3pm" is the next afternoon
        expected = datetime(2026, 11, 3, 15, 0, tzinfo=ZoneInfo(tz))
    moment, parse = read(text, tz)
    assert parse == "ok" and moment == expected


def test_relative_phrases_count_from_when_the_message_arrived_not_from_now():
    late = ARRIVED - timedelta(days=2)  # processed long after it came in
    moment, parse = read("within 5 minutes", arrived=late)
    assert parse == "ok" and moment == late + timedelta(minutes=5)


def test_a_within_2_hours_request_at_23_30_is_before_1_30_not_a_date():
    arrived = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)  # 23:30 in Kolkata
    moment, _ = read("within 2 hours", arrived=arrived)
    assert moment == arrived + timedelta(hours=2)


@pytest.mark.parametrize("text", ["tonight", "tomorrow morning"])
def test_day_parts_give_a_moment_on_that_day(text):
    moment, parse = read(text)
    assert parse == "ok" and moment is not None and moment > ARRIVED
    local = moment.astimezone(ZoneInfo("Asia/Kolkata"))
    assert local.date() == (datetime(2026, 11, 3).date() if text == "tonight" else datetime(2026, 11, 4).date())


def test_asap_has_no_time_so_the_words_are_kept_and_marked_unknown():
    assert read("as soon as possible") == (None, "unknown")
    assert read("ASAP") == (None, "unknown")


def test_no_deadline_text_is_not_a_failure():
    assert read("   ") == (None, "")


def test_a_moment_already_gone_is_marked_past(monkeypatch):
    import superaide.intentions.when as when

    monkeypatch.setattr(when, "find_moment", lambda text, now: (ARRIVED - timedelta(hours=1), ""))
    moment, parse = read("at 8am")
    assert moment is None and parse == "past"


# --- stored on the row, and ranked by it ---------------------------------------------------------


def say(model: Receptionist, text: str, deadline_text: str, who: str) -> None:
    model.out = ReceptionTurn(
        reply="Noted.",
        take_message=TakenMessage(name="Meera", topic="Quote", message=text, deadline_text=deadline_text),
    )


def test_the_row_keeps_the_exact_moment_and_ranks_5_minutes_above_friday(ava, script):
    say(script, "I need it within 5 minutes", "within 5 minutes", "U_MEERA_1")
    ava.handle(hello("I need it within 5 minutes", "U_MEERA_1"))
    say(script, "any time by Friday is fine", "by Friday", "U_RAHUL_1")
    ava.handle(hello("any time by Friday is fine", "U_RAHUL_1"))
    rows = {m.deadline_text: m for m in taken()}
    soon, friday = rows["within 5 minutes"], rows["by Friday"]
    assert soon.deadline_parse == "ok" and soon.deadline_at is not None
    arrived = soon.created_at
    assert timedelta(minutes=4) <= soon.deadline_at - arrived <= timedelta(minutes=6)
    assert soon.rank == "urgent" and "deadline_2h" in soon.rule_hits  # the sub-day deadline reaches the rules
    assert friday.deadline_at is not None and friday.deadline_at > soon.deadline_at
    assert friday.deadline_at - friday.created_at > timedelta(hours=2)
    assert "deadline_2h" not in friday.rule_hits


def test_unreadable_words_are_kept_with_an_unknown_mark_and_are_not_evidence_of_urgency(ava, script):
    say(script, "I need this ASAP", "ASAP", "U_MEERA_1")
    ava.handle(hello("I need this ASAP", "U_MEERA_1"))
    [m] = taken()
    assert (m.deadline_text, m.deadline_at, m.deadline_parse) == ("ASAP", None, "unknown")
    assert "deadline_2h" not in m.rule_hits


def test_the_inbox_api_shows_the_moment(owner_world, ava, script):
    say(script, "within 5 minutes please", "within 5 minutes", "U_MEERA_1")
    ava.handle(hello("within 5 minutes please", "U_MEERA_1"))
    body = owner_world["client"].get("/app/receptionist/messages").json()
    [m] = body["messages"]
    assert m["deadline_parse"] == "ok" and m["deadline_at"]
    with session_scope() as s:
        row = s.scalar(select(ReceptionMessage))
        assert row is not None and row.deadline_at is not None
        assert datetime.fromisoformat(m["deadline_at"]) == row.deadline_at
