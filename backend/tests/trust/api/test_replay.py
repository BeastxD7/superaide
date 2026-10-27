"""'Why did you say that?' (#122): ablation replay shows which notes changed an answer, inside the spend cap."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from superaide.brain.spend.budget import BudgetExceeded
from superaide.identity.auth.models import UserAccount
from superaide.identity.models import Person
from superaide.memory.knowledge.store import remember_fact
from superaide.memory.models import Event
from superaide.platform.db.session import session_scope
from superaide.trust import replay

BLUEBIRD = "Project Bluebird launches in March."
TEA = "The owner likes green tea."


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "why_replay_enabled", True)


@pytest.fixture
def scene(owner, agent):
    """The owner has two notes and one answered question; the fake model's answer depends on the Bluebird note."""

    def responder(purpose, system, messages, output_model):
        text = "\n".join(b.text for b in system)
        if purpose == "replay":
            return "The launch is in March." if "Bluebird" in text else "I am not sure."
        return "ok"

    agent.llm.responder = responder
    with session_scope() as s:
        me = s.get(Person, s.scalar(select(UserAccount.person_id)))
        for content in (BLUEBIRD, TEA):
            remember_fact(s, owner=me, content=content, category="work")
    assert owner.post("/app/chat", json={"text": "When does the launch happen?"}).status_code == 200
    with session_scope() as s:
        reply = s.scalar(select(Event).where(Event.direction == "out").order_by(Event.created_at.desc()))
        return str(reply.id)


def test_off_by_default(owner):
    assert owner.get(f"/app/why/{uuid.uuid4()}").status_code == 404
    assert owner.post(f"/app/why/{uuid.uuid4()}").status_code == 404


def test_the_note_that_changed_the_answer_is_found_and_the_idle_one_is_unused(owner, on, scene, agent):
    r = owner.post(f"/app/why/{scene}")
    assert r.status_code == 200, r.text
    notes = {n["text"]: n for n in r.json()["notes"]}
    assert notes[BLUEBIRD]["changed_the_answer"] is True and notes[BLUEBIRD]["influence"] > 0.5
    assert notes[TEA]["changed_the_answer"] is False and notes[TEA]["influence"] == 0.0
    assert len(r.json()["never_used"]) == 1
    assert owner.get(f"/app/why/{scene}").json()["notes"][0]["text"] == BLUEBIRD  # stored, most influential first


def test_the_number_of_model_calls_is_capped(owner, on, scene, agent, monkeypatch, settings):
    monkeypatch.setattr(settings, "replay_max_ablations", 1)
    before = len([c for c in agent.llm.calls if c["purpose"] == "replay"])
    owner.post(f"/app/why/{scene}")
    assert len([c for c in agent.llm.calls if c["purpose"] == "replay"]) - before == 2  # baseline + 1 ablation


def test_a_refused_call_stops_the_run_with_what_was_scored(owner, on, scene, agent):
    calls = {"n": 0}
    inner = agent.llm.responder

    def flaky(purpose, system, messages, output_model):
        calls["n"] += 1
        if calls["n"] == 3:
            raise BudgetExceeded("daily limit")
        return inner(purpose, system, messages, output_model)

    agent.llm.responder = flaky
    r = owner.post(f"/app/why/{scene}").json()
    assert r["stopped"].startswith("budget") and len(r["notes"]) == 1


def test_replay_uses_only_what_the_conversation_may_see(owner, on, scene, agent):
    with session_scope() as s:
        other = Person(display_name="Priya Tan", relation="contact")
        s.add(other)
        s.flush()
        remember_fact(s, owner=other, content="Priya's secret plan is a surprise party.", category="about")
    owner.post(f"/app/why/{scene}")
    sent = "\n".join(b.text for c in agent.llm.calls if c["purpose"] == "replay" for b in c["system"])
    assert "surprise party" not in sent


def test_someone_elses_or_unknown_replies_are_404(owner, on, scene):
    assert owner.post(f"/app/why/{uuid.uuid4()}").status_code == 404
    with session_scope() as s:
        question = s.scalar(select(Event).where(Event.direction == "in"))
        assert replay.distance("a b", "a b") == 0.0 and replay.distance("a", "b") == 1.0
    assert owner.post(f"/app/why/{question.id}").status_code == 404  # an inbound message is not a reply
