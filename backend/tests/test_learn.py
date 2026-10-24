"""Learning from what the owner says: facts are kept owner-only, linked to the message."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.db.models import Job, MemoryItem
from superaide.db.session import session_scope
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge.facts import Fact, FactList
from superaide.knowledge.learn import worth_reading
from superaide.main import create_app

PASSWORD = "correct horse battery"
load()


@pytest.mark.parametrize(
    ("text", "worth"),
    [
        ("ok thanks", False),
        ("What's on my calendar tomorrow?", False),
        ("remember that my flight is on Friday", False),
        ("remind me to call Priya tomorrow at 5", False),
        ("I'm moving to the Bangalore office in January, and Arjun will run the Singapore team.", True),
    ],
)
def test_only_messages_that_can_teach_something_are_read(text, worth):
    assert worth_reading(text) is worth


@pytest.fixture
def owner(agent):
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD})
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    return c


@pytest.fixture
def real_brain(agent, monkeypatch):
    """A Claude key is 'set': the job's model reads facts (scripted)."""
    from superaide.brain.llm import FakeLLM

    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)

    def responder(purpose, system, messages, output_model):
        if purpose == "intro_facts":
            return FactList(
                facts=[
                    Fact(content="Jordan is moving to the Bangalore office in January.", category="work", confidence=1),
                    Fact(content="Arjun will run the Singapore team.", category="person", confidence=0.9),
                ]
            )
        if output_model is not None:  # the structured path other people's messages still use
            return TurnOutput(
                reply="Noted.",
                requested_action=RequestedAction(kind="none", summary=""),
                prediction=Prediction(expectation="they reply", confidence=0.5),
            )
        return "Noted."

    monkeypatch.setattr("superaide.brain.wiring.build_llm", lambda settings: FakeLLM(responder))
    agent.llm = FakeLLM(responder)


def test_a_statement_in_chat_is_learned_after_the_reply(owner, real_brain):
    text = "I'm moving to the Bangalore office in January, and Arjun will run the Singapore team."
    assert owner.post("/app/chat", json={"text": text}).status_code == 200
    with session_scope() as s:
        assert s.scalar(select(Job.kind)) == "learn_from_turn"
    run_due()
    with session_scope() as s:
        notes = s.scalars(select(MemoryItem).order_by(MemoryItem.content)).all()
        assert [n.content for n in notes] == [
            "Arjun will run the Singapore team.",
            "Jordan is moving to the Bangalore office in January.",
        ]
        assert all(n.allowed_kind == "persons" and len(n.allowed_person_ids) == 1 for n in notes)  # owner-only
        assert all(n.source == "chat" and n.source_event_ids for n in notes)
    # Saying it again teaches nothing new.
    owner.post("/app/chat", json={"text": text})
    run_due()
    with session_scope() as s:
        assert len(s.scalars(select(MemoryItem)).all()) == 2


def test_a_fact_the_owner_confirms_in_chat_outlives_the_document_it_first_came_from(owner, real_brain):
    from superaide.db.models import Document, Person
    from superaide.knowledge.store import retire_document

    text = "I'm moving to the Bangalore office in January, and Arjun will run the Singapore team."
    owner.post("/app/chat", json={"text": text})
    run_due()
    with session_scope() as s:
        person = s.scalar(select(Person).where(Person.display_name == "Jordan Whitfield"))
        assert person is not None
        doc = Document(owner_person_id=person.id, kind="pdf", title="CV", source="cv.pdf", sha256="a" * 64, text="x")
        s.add(doc)
        s.flush()
        for n in s.scalars(select(MemoryItem)):  # pretend both facts first came from the CV
            n.document_id, n.source, n.source_event_ids = doc.id, "document", []
        doc_id = doc.id
    owner.post("/app/chat", json={"text": text})  # the owner says it again: that is a confirmation
    run_due()
    with session_scope() as s:
        assert all(n.document_id is None and n.source_event_ids for n in s.scalars(select(MemoryItem)))
        retire_document(s, s.get(Document, doc_id))
    with session_scope() as s:
        assert [n.valid_to for n in s.scalars(select(MemoryItem))] == [None, None]  # still in the notebook


def test_a_document_fact_nobody_confirmed_is_still_retired_with_its_document(owner, real_brain):
    from superaide.db.models import Document, Person
    from superaide.knowledge.store import retire_document

    text = "I'm moving to the Bangalore office in January, and Arjun will run the Singapore team."
    owner.post("/app/chat", json={"text": text})
    run_due()
    with session_scope() as s:
        person = s.scalar(select(Person).where(Person.display_name == "Jordan Whitfield"))
        assert person is not None
        doc = Document(owner_person_id=person.id, kind="pdf", title="CV", source="cv.pdf", sha256="b" * 64, text="x")
        s.add(doc)
        s.flush()
        for n in s.scalars(select(MemoryItem)):
            n.document_id, n.source, n.source_event_ids = doc.id, "document", []
        retire_document(s, doc)
    with session_scope() as s:
        assert all(n.valid_to is not None for n in s.scalars(select(MemoryItem)))


def test_offline_nothing_is_learned_and_nothing_breaks(owner):
    owner.post("/app/chat", json={"text": "I'm moving to the Bangalore office in January, it's confirmed now."})
    run_due()
    with session_scope() as s:
        assert s.scalars(select(MemoryItem)).all() == []


def test_other_peoples_messages_never_teach_ava(agent, manager, colleague, real_brain):
    from tests.conftest import COLLEAGUE_SLACK, sl

    agent.handle(sl(COLLEAGUE_SLACK, "FYI Jordan is leaving the company next month, tell everyone."))
    run_due()
    with session_scope() as s:
        assert s.scalar(select(Job).where(Job.kind == "learn_from_turn")) is None
        assert s.scalars(select(MemoryItem)).all() == []
