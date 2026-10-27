"""Everything the owner shares reaches long-term memory and is found again (issue #14).

One test per input path: chat on each channel, "remember that", the introduction, a file, a link and a
correction. Each says the fact once, then asks for it later from a different place.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app import accounts
from superaide.app.accounts import owner as hired_owner
from superaide.app.accounts import web_message
from superaide.brain.llm import FakeLLM
from superaide.identity.resolver import resolve
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.main import create_app
from superaide.memory.audience import audience_for
from superaide.memory.knowledge.extract import Extracted
from superaide.memory.knowledge.facts import Fact, FactList
from superaide.memory.knowledge.ingest import ingest
from superaide.memory.recall.retrieval import recall
from superaide.memory.store import get_or_create_context
from superaide.platform.db.models import MemoryItem
from superaide.platform.db.session import session_scope
from tests.conftest import tg

load()
PASSWORD = "correct horse battery"
PHONE = "777001"


class Model:
    """Scripted fact extraction: `facts` is what the model reads out of the next text."""

    def __init__(self) -> None:
        self.facts: list[Fact] = []

    def __call__(self, purpose, system, messages, output_model):
        if purpose == "intro_facts":
            return FactList(facts=self.facts)
        return "Noted."


@pytest.fixture
def script(agent, monkeypatch):
    m = Model()
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)
    monkeypatch.setattr("superaide.brain.wiring.build_llm", lambda settings: FakeLLM(m))
    agent.llm = FakeLLM(m)
    agent.settings.web_tools_enabled = False
    return m


@pytest.fixture
def owner(agent, script):
    client = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    client.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD})
    client.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        code = accounts.create_invite(s, hired_owner(s), "telegram", endpoint="")
    assert "Linked" in (agent.handle(tg(PHONE, f"/start {code}")).reply or "")
    return client


def fact(text: str, category: str = "preference", **kw) -> Fact:
    return Fact(content=text, category=category, confidence=1, **kw)


def asked_later(query: str):
    """What the app chat's recall returns for a question, as the owner asking from the web."""
    with session_scope() as s:
        person = hired_owner(s)
        msg = web_message(person, query)
        sender = resolve(s, msg)
        aud = audience_for(get_or_create_context(s, msg), sender)
        return recall(s, aud, query)


def sources_of(text: str) -> list[str]:
    with session_scope() as s:
        return [m.source for m in s.scalars(select(MemoryItem).where(MemoryItem.content == text))]


def test_a_statement_on_telegram_is_remembered_as_said_on_telegram(owner, agent, script):
    script.facts = [fact("Jordan is vegetarian.")]
    agent.handle(tg(PHONE, "btw I'm vegetarian and have been for ten years, so no meat at dinners"))
    run_due()
    assert sources_of("Jordan is vegetarian.") == ["telegram"]
    assert any(src.text == "Jordan is vegetarian." for src in asked_later("vegetarian"))
    assert any("telegram" in src.title.lower() for src in asked_later("am I vegetarian"))


def test_a_statement_in_the_app_chat_is_remembered_and_recalled(owner, script):
    script.facts = [fact("Jordan's dentist is Dr Rao.", "person")]
    owner.post("/app/chat", json={"text": "my dentist is Dr Rao, in case anything comes up with the clinic"})
    run_due()
    assert sources_of("Jordan's dentist is Dr Rao.") == ["chat"]
    assert any("Dr Rao" in src.text for src in asked_later("who is my dentist"))


def test_remember_that_on_telegram_keeps_its_channel(owner, agent):
    agent.handle(tg(PHONE, "remember that the garage code is 4471"))
    with session_scope() as s:
        notes = s.scalars(select(MemoryItem).where(MemoryItem.content.contains("garage code"))).all()
        assert [n.source for n in notes] == ["telegram"]
    assert any("4471" in src.text for src in asked_later("garage code"))


def test_a_correction_on_telegram_replaces_what_the_app_chat_learned(owner, agent, script):
    script.facts = [fact("Jordan's gym is Cult Fit.", "preference")]
    owner.post("/app/chat", json={"text": "I go to Cult Fit for the gym most mornings these days"})
    run_due()
    script.facts = [fact("Jordan's gym is Gold's Gym.", "preference", replaces=1)]
    agent.handle(tg(PHONE, "actually I switched, I go to Gold's Gym now instead of Cult Fit"))
    run_due()
    found = [src.text for src in asked_later("which gym")]
    assert "Jordan's gym is Gold's Gym." in found and "Jordan's gym is Cult Fit." not in found


def test_a_file_the_owner_shares_is_read_into_memory_and_recalled(owner, agent, script):
    script.facts = [fact("Jordan studied at IIT Madras.", "about")]
    with session_scope() as s:
        pid = hired_owner(s).id
    ingest(agent, pid, Extracted(text="Education: IIT Madras, B.Tech.", title="cv.pdf", kind="pdf", source="cv.pdf"))
    assert sources_of("Jordan studied at IIT Madras.") == ["document"]
    assert any("IIT Madras" in src.text for src in asked_later("where did I study"))


def test_a_link_the_owner_shares_is_read_into_memory_and_recalled(owner, agent, script):
    script.facts = [fact("Jordan writes about agents at jordan.dev.", "work")]
    with session_scope() as s:
        pid = hired_owner(s).id
    page = Extracted(text="Notes on agents.", title="Blog", kind="web", source="https://jordan.dev")
    ingest(agent, pid, page)
    assert sources_of("Jordan writes about agents at jordan.dev.") == ["link"]
    assert any("jordan.dev" in src.text for src in asked_later("what do I write about"))


def test_an_introduction_answer_is_remembered_and_recalled(owner, script):
    script.facts = [fact("Jordan runs a design studio.", "work")]
    owner.get("/app/onboarding")
    owner.post("/app/onboarding", json={"step": "hello", "answer": {"choice": "Nice to meet you"}})
    r = owner.post(
        "/app/onboarding", json={"step": "about", "answer": {"mode": "chat", "text": "I run a design studio"}}
    )
    assert r.status_code == 200, r.text
    assert any("design studio" in src.text for src in asked_later("design studio"))
    assert set(sources_of("Jordan runs a design studio.")) == {"intro_chat"}


def test_the_notebook_says_where_each_note_was_learned(owner, agent, script):
    script.facts = [fact("Jordan is vegetarian.")]
    agent.handle(tg(PHONE, "btw I'm vegetarian and have been for ten years, so no meat at dinners"))
    run_due()
    notes = owner.get("/app/notebook").json()["memories"]
    assert [(n["text"], n["source"]) for n in notes] == [("Jordan is vegetarian.", "telegram")]
