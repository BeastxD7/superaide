"""Importance, decay only for time-bound facts, and spaced "is this still true?" (#49)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.context.audience import Audience
from superaide.context.recall import recall_memories
from superaide.db.models import MemoryItem, Person
from superaide.db.session import session_scope
from superaide.knowledge.store import remember_fact
from superaide.memory import importance, recheck
from superaide.proactive import digest

NOW = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)


def owner(s) -> Person:
    p = Person(display_name="Jordan Whitfield", relation="unknown")
    s.add(p)
    s.flush()
    return p


def aged(item: MemoryItem, days: float) -> MemoryItem:
    item.created_at = item.valid_from = NOW - timedelta(days=days)
    return item


def test_importance_and_time_bound_are_set_in_code_from_the_category():
    with session_scope() as s:
        o = owner(s)
        role = remember_fact(s, owner=o, content="Jordan is the CEO of Orchard Labs.", category="about")
        free = remember_fact(s, owner=o, content="Jordan is free for calls.", category="availability")
        trip = remember_fact(s, owner=o, content="Jordan is travelling to Delhi on Friday.", category="work")
        assert (role.importance, role.time_bound) == (0.8, False)
        assert free.time_bound and free.importance < role.importance
        assert trip.time_bound  # the wording says it fades even though the category doesn't
        clamped = remember_fact(s, owner=o, content="Jordan collects stamps.", category="other", importance=9.0)
        assert clamped.importance <= 1.0


def test_the_model_may_only_nudge_importance_and_time_bound():
    assert importance.score("other", 1.0) == 0.7  # half code, half suggestion
    assert importance.score("about", 0.0) == 0.4
    assert importance.score("about") == 0.8


def test_a_time_bound_fact_fades_but_an_about_fact_does_not():
    assert importance.decay(False, NOW - timedelta(days=400), NOW) == 1.0
    assert importance.decay(True, NOW, NOW) == 1.0
    assert importance.decay(True, NOW - timedelta(days=14), NOW) == pytest.approx(0.5)
    assert importance.decay(True, NOW - timedelta(days=900), NOW) == importance.DECAY_FLOOR


def test_old_time_bound_note_stops_outranking_a_fresher_one_but_an_old_about_fact_keeps_its_place():
    with session_scope() as s:
        o = owner(s)
        aud = Audience(person_ids={o.id}, min_trust=2)
        old_plan = aged(
            remember_fact(s, owner=o, content="Jordan's offsite plan: Goa is the venue.", category="availability"), 70
        )
        new_plan = aged(
            remember_fact(s, owner=o, content="Jordan's offsite plan: Lisbon is the venue.", category="availability"),
            1,
        )
        old_role = aged(remember_fact(s, owner=o, content="Jordan's role: founder of Orchard.", category="about"), 400)
        new_role = aged(remember_fact(s, owner=o, content="Jordan's role: advisor to Orchard.", category="other"), 1)
        s.flush()
        plans = [r.text for r in recall_memories(s, aud, "offsite plan venue")]
        assert plans[0].startswith("Jordan's offsite plan: Lisbon"), plans
        roles = [r.text for r in recall_memories(s, aud, "Jordan's role at Orchard")]
        assert roles[0].endswith("founder of Orchard."), roles  # importance 0.8 beats "other" 0.4, however old
        assert old_plan.time_bound and not old_role.time_bound and new_plan and new_role


def test_default_notes_weigh_one_so_ranking_is_unchanged_for_them():
    plain = MemoryItem(content="x", importance=0.5, time_bound=False, created_at=NOW - timedelta(days=300))
    assert importance.weight(plain, NOW) == 1.0
    assert importance.weight(object(), NOW) == 1.0  # a message row has no importance


def test_a_locked_time_bound_note_never_decays():
    old = MemoryItem(content="x", importance=0.5, time_bound=True, created_at=NOW - timedelta(days=300))
    assert importance.weight(old, NOW) < 0.1  # an ordinary time-bound note has faded away
    old.locked = True
    assert importance.weight(old, NOW) == 1.0  # locked by the owner: it counts as on the day it was said


# --- spaced re-verification ---------------------------------------------------------------


@pytest.fixture
def on(settings, monkeypatch):
    monkeypatch.setattr(settings, "memory_recheck_enabled", True)
    monkeypatch.setattr(settings, "memory_recheck_per_week", 2)


def make(s, o, text, category="about", days=100.0, **kw):
    return aged(remember_fact(s, owner=o, content=text, category=category, **kw), days)


def test_only_important_lasting_owner_facts_are_due_after_their_interval(on):
    with session_scope() as s:
        o = owner(s)
        role = make(s, o, "Jordan is the CEO of Orchard Labs.")
        make(s, o, "Jordan likes jazz.", "other", 100)  # not important enough
        make(s, o, "Jordan is out of office until Monday.", "availability", 100)  # fades, never asked
        make(s, o, "Jordan is the founder of Beta Labs.", days=10)  # not due yet
        shared = make(s, o, "Jordan speaks at the summit.", "work", 100, shareable=True)
        s.flush()
        assert [m.id for m in recheck.due(s, o, NOW)] == [role.id] and shared


def test_intervals_expand_with_each_confirmation(on):
    assert [recheck.interval(n).days for n in (0, 1, 2, 3, 9)] == [30, 90, 180, 365, 365]
    with session_scope() as s:
        o = owner(s)
        m = make(s, o, "Jordan is the CEO of Orchard Labs.", days=400)
        m.verify_count, m.last_verified_at = 1, NOW - timedelta(days=60)
        s.flush()
        assert recheck.due(s, o, NOW) == []
        m.last_verified_at = NOW - timedelta(days=91)
        assert recheck.due(s, o, NOW) == [m]


def test_at_most_n_questions_a_week_and_none_when_the_flag_is_off(on, settings, monkeypatch):
    with session_scope() as s:
        o = owner(s)
        for i in range(5):
            make(s, o, f"Jordan's colleague number {i} is Priya{i}.", "person")
        s.flush()
        assert len(recheck.due(s, o, NOW)) == 2
        lines = recheck.digest_lines(s, o, NOW)
        assert lines[0].startswith("**Still true?**") and len(lines) == 3
        assert recheck.digest_lines(s, o, NOW + timedelta(days=1)) == []  # the week's allowance is used
        assert len(recheck.digest_lines(s, o, NOW + timedelta(days=8))) == 3  # next week: two more
        monkeypatch.setattr(settings, "memory_recheck_enabled", False)
        assert recheck.digest_lines(s, o, NOW + timedelta(days=30)) == []


def test_the_question_shows_up_in_the_digest_and_never_as_an_interruption(on):
    with session_scope() as s:
        o = owner(s)
        make(s, o, "Jordan does not eat shellfish.", "preference")
        s.flush()
        text = digest.compose(s, o, NOW, "morning", events=[])
        assert text and "Still true?" in text and "shellfish" in text


def test_yes_pushes_the_next_check_out_no_retires_changed_replaces(on):
    with session_scope() as s:
        o = owner(s)
        keep = make(s, o, "Jordan does not eat shellfish.", "preference")
        gone = make(s, o, "Jordan is the CEO of Orchard Labs.")
        moved = make(s, o, "Jordan lives in Mumbai.", "about")
        s.flush()
        recheck.answer(s, o, keep.id, "yes")
        assert keep.verify_count == 1 and keep.last_verified_at and keep.valid_to is None
        recheck.answer(s, o, gone.id, "no")
        assert gone.valid_to is not None
        new = recheck.answer(s, o, moved.id, "changed", "Jordan lives in Pune.")
        assert moved.valid_to is not None and new.content == "Jordan lives in Pune." and new.valid_to is None
        assert new.verify_count == 1
        with pytest.raises(ValueError):
            recheck.answer(s, o, new.id, "changed", "  ")
        with pytest.raises(ValueError):
            recheck.answer(s, o, new.id, "maybe")
        with pytest.raises(LookupError):
            recheck.answer(s, o, gone.id, "yes")  # already retired
        stranger = owner(s)
        with pytest.raises(LookupError):
            recheck.answer(s, stranger, new.id, "yes")  # someone else's note
        assert s.scalar(select(MemoryItem.id).where(MemoryItem.id == new.id))
