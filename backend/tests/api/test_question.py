"""One Good Question (#121): at most one clarifying question a day, chosen by uncertainty x impact."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from superaide.db.models import MemoryItem, Person, UserAccount
from superaide.db.session import session_scope
from superaide.knowledge.store import remember_fact
from superaide.trust import questions
from superaide.trust.models import OwnerQuestion


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "one_good_question_enabled", True)


def owner_id():
    with session_scope() as s:
        return s.scalar(select(UserAccount.person_id))


def test_off_by_default(owner):
    assert owner.get("/app/question").status_code == 404
    assert owner.post("/app/question/ask").status_code == 404


def test_the_most_uncertain_high_impact_topic_is_chosen(owner, on):
    q = owner.post("/app/question/ask").json()["question"]
    assert q["topic"] == "urgent_topics"  # nothing known, highest impact
    with session_scope() as s:
        row = s.get(OwnerQuestion, uuid.UUID(q["id"]))
        assert row.score == 1.0 and row.selected_by == "uncertainty"


def test_knowing_a_topic_well_moves_the_question_elsewhere(owner, on):
    with session_scope() as s:
        o = s.get(Person, owner_id())
        remember_fact(s, owner=o, content="Urgent for me: anything from the board.", category="urgency", confidence=1.0)
    assert owner.post("/app/question/ask").json()["question"]["topic"] == "availability"


def test_only_one_question_a_day(owner, on):
    first = owner.post("/app/question/ask").json()["question"]
    again = owner.post("/app/question/ask").json()["question"]
    assert again["id"] == first["id"]
    assert owner.get("/app/question").json()["question"]["id"] == first["id"]
    # The database itself refuses a second row for the same person and day.
    with pytest.raises(IntegrityError), session_scope() as s:
        row = s.get(OwnerQuestion, uuid.UUID(first["id"]))
        s.add(OwnerQuestion(person_id=row.person_id, asked_on=row.asked_on, topic="x", question="y"))


def test_a_new_day_allows_a_new_question_but_never_a_repeat(owner, on):
    first = owner.post("/app/question/ask").json()["question"]
    with session_scope() as s:
        row = s.get(OwnerQuestion, uuid.UUID(first["id"]))
        row.asked_on = row.asked_on - timedelta(days=1)
    second = owner.post("/app/question/ask").json()["question"]
    assert second["id"] != first["id"] and second["topic"] != first["topic"]


def test_the_answer_becomes_a_first_hand_fact(owner, on):
    q = owner.post("/app/question/ask").json()["question"]
    r = owner.post(f"/app/question/{q['id']}/answer", json={"answer": "Anything from my daughter's school"})
    assert r.status_code == 200 and r.json()["question"]["status"] == "answered"
    with session_scope() as s:
        m = s.scalar(select(MemoryItem).where(MemoryItem.content.contains("daughter's school")))
        assert m.source == "owner_edit" and m.category == "urgency" and m.confidence == 1.0
        assert m.allowed_kind == "persons"  # private to the owner
    assert owner.post(f"/app/question/{q['id']}/answer", json={"answer": "again"}).status_code == 409
    assert owner.post(f"/app/question/{q['id']}/answer", json={"answer": "   "}).status_code in (409, 422)


def test_skip_and_never_ask_about_a_topic(owner, on):
    q = owner.post("/app/question/ask").json()["question"]
    assert owner.post(f"/app/question/{q['id']}/skip", json={"never": True}).json()["question"]["status"] == "skipped"
    with session_scope() as s:
        o = s.get(Person, owner_id())
        assert q["topic"] not in {t.key for _, t in questions.candidates(s, o)}


def test_nothing_left_to_ask_returns_null(owner, on):
    for n, _ in enumerate(questions.TOPICS, start=1):
        q = owner.post("/app/question/ask").json()["question"]
        assert q is not None
        owner.post(f"/app/question/{q['id']}/skip", json={})
        with session_scope() as s:
            row = s.get(OwnerQuestion, uuid.UUID(q["id"]))
            row.asked_on = row.asked_on - timedelta(days=30 + n)
    assert owner.post("/app/question/ask").json() == {"question": None}


def test_sensitive_wording_is_screened_and_templates_are_clean():
    assert all(questions.validate(t.question) for t in questions.TOPICS)
    for bad in ("What is your salary?", "Any health problems?", "Your bank password?", "Religion?", ""):
        assert not questions.validate(bad)


def test_someone_elses_question_is_404(owner, browser, on):
    q = owner.post("/app/question/ask").json()["question"]
    from tests.api.conftest import OTHER, signup

    other = browser()
    signup(other, **OTHER)
    assert other.post(f"/app/question/{q['id']}/answer", json={"answer": "x"}).status_code == 403  # not an owner
    assert owner.post(f"/app/question/{uuid.uuid4()}/skip", json={}).status_code == 404
