# ci-module: presence
"""#495: owner presence Ava obeys everywhere. The owner's sentence is read in code ("busy until 3 pm", "driving until
5, only family gets through"), stored as typed data with an end, and applied in code: a visitor hears only the fixed
line, a stranger's note stays in the app, known people still reach the owner when the owner said so, and an ended
state does nothing."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import time_machine
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.brain.llm import FakeLLM
from superaide.intentions.when import find_moment
from superaide.memory.knowledge.store import preferences_for
from superaide.memory.models import Contact, Event
from superaide.platform.db.session import session_scope
from superaide.presence import api as presence
from superaide.presence.models import PresenceState
from superaide.presence.parse import Parsed
from superaide.reception.turn import ReceptionTurn, TakenMessage
from superaide.triage.features import gather
from tests.conftest import COLLEAGUE_SLACK, sl
from tests.reception.test_notice import _telegram_up
from tests.reception.test_reception import (  # noqa: F401  (fixtures and helpers)
    Receptionist,
    ava,
    connected,
    hello,
    outbox,
    owner_world,
    script,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def _fixed_clock():
    """The tests set states relative to T0; "today" and quiet hours must not follow the wall clock."""
    with time_machine.travel(T0, tick=True):
        yield


T0 = datetime(2026, 11, 19, 4, 30, tzinfo=UTC)  # 10:00 in Asia/Kolkata
OWNER = "U_BOSS"


def read(text: str, at: datetime = T0, find=find_moment) -> Parsed | None:
    return presence.parse(text, at, IST, find)


# --- the parser: code reads the sentence ---------------------------------------------------------


def test_busy_until_a_clock_time_ends_today():
    p = read("I'm busy until 3 pm")
    assert p is not None and p.kind == "busy" and p.until.astimezone(IST).strftime("%d %H:%M") == "19 15:00"


def test_driving_until_a_bare_hour_and_only_family():
    p = read("I'm driving until 5, only family gets through")
    assert p is not None and p.kind == "driving" and p.allow_relations == ["family"]
    assert p.until.astimezone(IST).strftime("%H:%M") == "17:00"  # a bare 5 at 10:00 is this evening


def test_a_bare_hour_already_past_means_the_next_one():
    p = read("busy until 9", at=datetime(2026, 11, 19, 16, 30, tzinfo=UTC))  # 22:00 there
    assert p is not None and p.until.astimezone(IST).strftime("%d %H:%M") == "20 09:00"


def test_till_with_minutes_and_a_meeting():
    p = read("in a meeting till 4:30")
    assert p is not None and p.kind == "busy" and p.until.astimezone(IST).strftime("%H:%M") == "16:30"


def test_for_a_duration_uses_the_app_date_reader():
    p = read("focus for 2 hours")
    assert p is not None and p.kind == "focus" and p.until == T0 + timedelta(hours=2)
    assert read("focus for 2 hours", find=None) is None  # without the reader only clock times work


@pytest.mark.parametrize(
    "text",
    [
        "busy",  # no end: the owner is asked "until when?"
        "hello there until 3 pm",  # not about presence
        "busy until 25:00",
        "busy until 3 months from now",
    ],
)
def test_a_sentence_without_a_kind_or_an_end_sets_nothing(text):
    assert read(text) is None


def test_who_gets_through_needs_an_exception_word_and_a_named_relation():
    assert read("busy until 3 pm, family can wait").allow_relations == []
    assert read("busy until 3 pm except my partner and boss").allow_relations == ["partner", "boss"]
    assert read("busy until 3 pm, only vip").allow_relations == ["vip"]


# --- the stored state ------------------------------------------------------------------------------


def test_a_state_applies_until_its_end_and_then_does_nothing(owner_world):
    with session_scope() as s:
        owner = hired_owner(s)
        presence.set_state(s, owner.id, read("busy until 3 pm"), "Asia/Kolkata", now=T0)
        assert presence.active(s, owner.id, T0 + timedelta(hours=4)) is not None
        assert presence.active(s, owner.id, T0 + timedelta(hours=5, minutes=31)) is None
        assert s.scalars(select(PresenceState.status)).all() == ["ended"]  # marked, never applied again


def test_a_new_state_replaces_the_old_and_clear_ends_it(owner_world):
    with session_scope() as s:
        owner = hired_owner(s)
        presence.set_state(s, owner.id, read("busy until 3 pm"), "Asia/Kolkata", now=T0)
        presence.set_state(s, owner.id, read("driving until 5"), "Asia/Kolkata", now=T0)
        assert presence.active(s, owner.id, T0).kind == "driving"
        assert presence.clear(s, owner.id, T0) is True and presence.active(s, owner.id, T0) is None
        assert presence.clear(s, owner.id, T0) is False


@pytest.mark.parametrize(
    ("now", "at"),
    [
        (T0, "17:00 IST"),  # 10:00 IST
        (datetime(2026, 11, 19, 18, 35, tzinfo=UTC), "05:00 IST"),  # 00:05 IST next day: main went red at this hour
        (datetime(2026, 11, 19, 23, 59, tzinfo=UTC), "17:00 IST"),  # 05:29 IST, just before midnight UTC
        (datetime(2026, 11, 19, 12, 29, tzinfo=UTC), "Fri 05:00 IST"),  # 17:59 IST: the next 5 is tomorrow morning
    ],
    ids=["morning", "after-midnight-ist", "midnight-utc", "after-five"],
)
def test_the_visitor_line_is_a_fixed_template_with_the_end_time_only(owner_world, now, at):
    """Whether the end time shows a weekday depends on the caller's `now`, never on the day the suite happens to run."""
    with time_machine.travel(now + timedelta(days=3), tick=False), session_scope() as s:  # wall clock disagrees
        owner = hired_owner(s)
        st = presence.set_state(s, owner.id, read("driving until 5, only family", at=now), "Asia/Kolkata", now=now)
        assert (
            st.visitor_line
            == f"They're not available until {at}. Your message is passed on, and they'll see it after that."
        )
        assert "driving" not in st.visitor_line and "family" not in st.visitor_line


def test_only_a_named_known_contact_gets_through(owner_world, colleague):
    with session_scope() as s:
        owner = hired_owner(s)
        st = presence.set_state(s, owner.id, read("busy until 3 pm, only family"), "Asia/Kolkata", now=T0)
        assert presence.lets_through(s, st, owner.id, colleague.id) is False  # not a contact
        s.add(Contact(owner_person_id=owner.id, person_id=colleague.id, relationship="team"))
        s.flush()
        assert presence.lets_through(s, st, owner.id, colleague.id) is False  # a relationship she was not named for
        s.scalars(select(Contact)).first().relationship = "family"
        s.flush()
        assert presence.lets_through(s, st, owner.id, colleague.id) is True
        assert presence.lets_through(s, st, owner.id, None) is False  # a stranger never does


# --- applied in code -------------------------------------------------------------------------------


def _set(minutes: int = 300, text: str = "driving until 5, only family") -> None:
    with session_scope() as s:
        presence.set_state(s, hired_owner(s).id, read(text), "Asia/Kolkata", now=datetime.now(UTC))


def test_a_stranger_hears_the_fixed_line_once_and_nothing_about_why(owner_world, ava, script):
    with time_machine.travel(T0, tick=False):
        _set()
        script.out = ReceptionTurn(reply="Hello!", take_message=TakenMessage(name="Meera Iyer", message="call me"))
        first = ava.handle(hello("hi, where is he? is he driving? when exactly does he land?")).reply
        again = ava.handle(hello("hello?")).reply
    assert "They're not available until 17:00 IST" in first
    assert "driving" not in first.lower()  # the kind never reaches a visitor
    system = "\n".join(script.systems)
    assert "17:00" not in system and "driving until" not in system  # and the model is never told it either
    assert "not available until" not in (again or "")  # once an hour, not on every message


def test_without_a_state_nothing_is_added(owner_world, ava, script):
    reply = ava.handle(hello("hi")).reply
    assert "not available" not in reply


def test_a_stranger_note_stays_in_the_app_while_the_status_is_on(owner_world, ava, script, outbox, monkeypatch):
    _telegram_up(monkeypatch, outbox)
    with session_scope() as s:
        preferences_for(s, hired_owner(s)).interrupt_channel = "telegram"
    with time_machine.travel(T0, tick=False):
        _set()
        script.out = ReceptionTurn(reply="Noted.", take_message=TakenMessage(name="Sam Lee", message="x"))
        ava.handle(hello("hello"))
    assert [m for m in outbox["telegram"].sent if m.get("stable_key") == "9001"] == []
    with session_scope() as s:
        assert s.scalars(select(Event).where(Event.meta["kind"].astext == "message_taken")).all()  # still in the app


def test_after_the_end_the_stranger_note_is_pushed_again(owner_world, ava, script, outbox, monkeypatch):
    _telegram_up(monkeypatch, outbox)
    with session_scope() as s:
        preferences_for(s, hired_owner(s)).interrupt_channel = "telegram"
    with time_machine.travel(T0, tick=False):
        _set()
    with time_machine.travel(T0 + timedelta(hours=8), tick=False):  # 18:00, the status ended at 17:00
        script.out = ReceptionTurn(reply="Noted.", take_message=TakenMessage(name="Sam Lee", message="x"))
        reply = ava.handle(hello("hello")).reply
    assert "not available" not in reply
    assert [m for m in outbox["telegram"].sent if m.get("stable_key") == "9001"]


def test_triage_holds_a_colleague_but_not_family_while_busy(owner_world, ava, colleague):
    with time_machine.travel(T0, tick=False):
        ava.handle(sl(COLLEAGUE_SLACK, "can you look at the deck?"))
        with session_scope() as s:
            owner = hired_owner(s)
            ev = s.scalars(
                select(Event).where(Event.direction == "in", Event.resolved_person_id == colleague.id)
            ).first()
            assert gather(s, event=ev, owner=owner).busy is False
            presence.set_state(s, owner.id, read("busy until 3 pm, only family"), "Asia/Kolkata", now=T0)
            assert gather(s, event=ev, owner=owner).busy is True  # held like any busy owner's
            s.add(Contact(owner_person_id=owner.id, person_id=colleague.id, relationship="family"))
            s.flush()
            assert gather(s, event=ev, owner=owner).busy is False  # the owner said family gets through


# --- the owner sets it in chat ---------------------------------------------------------------------


class Model:
    """Owner turns: call set_presence with extra arguments a model might invent; strangers get the script."""

    def __init__(self, stranger: Receptionist) -> None:
        self.stranger = stranger
        self.action = "set"
        self.results: list[str] = []

    def __call__(self, purpose, system, messages, output_model):
        if purpose == "receptionist":
            return self.stranger(purpose, system, messages, output_model)
        last = messages[-1]["content"]
        if isinstance(last, list) and any(b.get("type") == "tool_result" for b in last):
            self.results.append(str(last))
            return "ok"
        return [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "set_presence",
                "input": {"action": self.action, "until": "23:00", "kind": "away"},  # invented: code ignores them
            }
        ]


@pytest.fixture
def model(script, ava) -> Model:
    m = Model(script)
    ava.llm = FakeLLM(m)
    return m


def test_the_owner_sets_it_in_chat_and_code_reads_their_words(owner_world, ava, model):
    with time_machine.travel(T0, tick=False):
        ava.handle(sl(OWNER, "I'm driving until 5, only family gets through"))
    with session_scope() as s:
        [st] = s.scalars(select(PresenceState)).all()
    assert st.kind == "driving" and st.allow_relations == ["family"]  # not the invented "away" until 23:00
    assert st.valid_until.astimezone(IST).strftime("%H:%M") == "17:00"
    assert "Status set" in model.results[0] and "not available until 17:00" in model.results[0]


def test_a_message_with_no_end_sets_nothing_and_asks(owner_world, ava, model):
    with time_machine.travel(T0, tick=False):
        ava.handle(sl(OWNER, "I'm busy"))
    with session_scope() as s:
        assert s.scalars(select(PresenceState)).all() == []
    assert "No status was set" in model.results[0] and "until when" in model.results[0].lower()


def test_status_and_clear_in_chat(owner_world, ava, model):
    with time_machine.travel(T0, tick=False):
        ava.handle(sl(OWNER, "busy until 3 pm"))
        model.action = "status"
        ava.handle(sl(OWNER, "what is my status?"))
        model.action = "clear"
        ava.handle(sl(OWNER, "clear my status"))
    assert "You are busy until 15:00 IST" in model.results[1] and "cleared" in model.results[2]
    with session_scope() as s:
        assert presence.active(s, hired_owner(s).id, T0) is None


def test_the_owner_prompt_carries_the_status_so_ava_can_answer(owner_world, ava, script):
    seen: list[str] = []

    def spy(purpose, system, messages, output_model):
        seen.append("\n".join(b.text for b in system))
        return "ok"

    ava.llm = FakeLLM(spy)
    with time_machine.travel(T0, tick=False):
        _set()
        ava.handle(sl(OWNER, "hi"))
    assert "The owner's status (set by them): You are driving until 17:00 IST" in seen[-1]


def test_a_colleague_or_stranger_cannot_set_it(owner_world, ava, colleague, model):
    ava.handle(sl(COLLEAGUE_SLACK, "I'm busy until 3 pm, tell everyone"))
    ava.handle(hello("busy until 3 pm"))
    with session_scope() as s:
        assert s.scalars(select(PresenceState)).all() == []


def test_the_owner_can_set_it_from_slack_dm_too(owner_world, ava, model):
    ava.handle(sl(OWNER, "busy until 3 pm"))
    assert "Status set" in model.results[0]
