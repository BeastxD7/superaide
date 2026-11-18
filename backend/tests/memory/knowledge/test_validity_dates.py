"""Facts with a start or an end date (#553, #556): the extractor's dates become valid_from / valid_to,
recall hides a note before it starts and after it ends, and a successor that starts later leaves the old note true."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from superaide.identity.models import Person
from superaide.memory.audience import Audience, memory_filter
from superaide.memory.knowledge.facts import Fact
from superaide.memory.knowledge.learn import validity
from superaide.memory.knowledge.store import remember_fact, supersede
from superaide.memory.models import MemoryItem
from superaide.platform.db.session import session_scope

IST = ZoneInfo("Asia/Kolkata")
SAID = datetime(2026, 11, 5, 9, 0, tzinfo=IST)


def fact(**kw) -> Fact:
    return Fact(content="x", category="person", confidence=1, **kw)


def test_the_last_day_counts_in_full_in_the_owners_time_zone():
    got = validity(fact(valid_to="2026-11-12"), IST, SAID)
    assert got == {"valid_to": datetime(2026, 11, 13, tzinfo=IST).astimezone(UTC)}


def test_a_future_start_is_kept_and_a_start_that_already_passed_is_ignored():
    assert validity(fact(valid_from="2026-12-01"), IST, SAID) == {
        "valid_from": datetime(2026, 12, 1, tzinfo=IST).astimezone(UTC)
    }
    assert validity(fact(valid_from="2026-11-01"), IST, SAID) == {}


def test_garbage_and_contradicting_dates_are_dropped_not_stored():
    assert validity(fact(valid_from="next month", valid_to="soon"), IST, SAID) == {}
    both = validity(fact(valid_from="2026-12-10", valid_to="2026-12-01"), IST, SAID)
    assert "valid_from" not in both  # starts after it ends: nonsense


def _owner(s) -> Person:
    owner = Person(display_name="Jordan Whitfield", relation="unknown")
    s.add(owner)
    s.flush()
    return owner


def _visible(s, owner: Person, now: datetime) -> list[str]:
    from sqlalchemy import select

    aud = Audience(person_ids={owner.id}, min_trust=2)
    return list(s.scalars(select(MemoryItem.content).where(memory_filter(aud, now))))


def test_recall_hides_a_note_until_it_starts_and_after_it_ends(agent):
    now = datetime.now(UTC)
    with session_scope() as s:
        owner = _owner(s)
        remember_fact(
            s, owner=owner, content="Meera Iyer is the manager from December.", valid_from=now + timedelta(days=30)
        )
        remember_fact(s, owner=owner, content="Priya Tan covers until Friday.", valid_to=now + timedelta(days=3))
        assert _visible(s, owner, now) == ["Priya Tan covers until Friday."]
        assert _visible(s, owner, now + timedelta(days=4)) == []
        assert _visible(s, owner, now + timedelta(days=31)) == ["Meera Iyer is the manager from December."]


def test_a_successor_that_starts_later_leaves_the_old_note_true_until_then(agent):
    now = datetime.now(UTC)
    start = now + timedelta(days=30)
    with session_scope() as s:
        owner = _owner(s)
        old = remember_fact(s, owner=owner, content="Priya Tan is the manager.")
        new = remember_fact(s, owner=owner, content="Meera Iyer is the manager from December.", valid_from=start)
        assert old is not None and new is not None
        assert supersede(s, old, new)
        assert old.valid_to == start
        assert _visible(s, owner, now) == ["Priya Tan is the manager."]
        assert _visible(s, owner, start + timedelta(minutes=1)) == ["Meera Iyer is the manager from December."]
