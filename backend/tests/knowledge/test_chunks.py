"""Document chunks: a fact on page 6 of a long file is found and cited with its passage (issue #47)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from superaide.app.accounts import owner as hired_owner
from superaide.app.accounts import web_message
from superaide.brain.embed import FakeEmbedder
from superaide.brain.llm import FakeLLM
from superaide.context.audience import Audience, audience_for
from superaide.context.recall import recall
from superaide.identity.resolver import resolve
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge import chunking
from superaide.knowledge.extract import Extracted
from superaide.knowledge.facts import MAX_INPUT_CHARS, Fact, FactList
from superaide.knowledge.ingest import ingest
from superaide.main import create_app
from superaide.memory.store import get_or_create_context
from superaide.platform.db.models import Document, DocumentChunk, MemoryItem
from superaide.platform.db.session import session_scope

load()
PASSWORD = "correct horse battery"
PHONE = "777001"
NEEDLE = "Jordan won the Lovelace Medal in 2019."


def long_text() -> str:
    pages = [f"Page {i}. " + ("Routine notes about the quarter. " * 150) for i in range(1, 11)]
    pages[5] = "Page 6. Awards. The Lovelace Medal was awarded to Jordan in 2019 for applied AI work."
    return "\n\n".join(pages)


class Script:
    """Reads a fact only out of the window that contains the needle, like a real model would."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, purpose, system, messages, output_model):
        if purpose != "intro_facts":
            return "Noted."
        body = messages[0]["content"]
        self.calls.append(body)
        facts = [Fact(content=NEEDLE, category="about", confidence=1)] if "Lovelace" in body else []
        return FactList(facts=facts)


@pytest.fixture
def script(agent, monkeypatch):
    m = Script()
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)
    monkeypatch.setattr("superaide.brain.wiring.build_llm", lambda settings: FakeLLM(m))
    agent.llm = FakeLLM(m)
    return m


@pytest.fixture
def owner(agent, script):
    client = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    client.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD})
    client.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    return client


def share(agent, text: str, title: str = "cv.pdf", kind: str = "pdf"):
    with session_scope() as s:
        pid = hired_owner(s).id
    return ingest(agent, pid, Extracted(text=text, title=title, kind=kind, source=title))


def asked(query: str):
    with session_scope() as s:
        msg = web_message(hired_owner(s), query)
        aud = audience_for(get_or_create_context(s, msg), resolve(s, msg))
        return recall(s, aud, query)


def test_the_splitter_keeps_passages_small_and_loses_no_word():
    text = long_text()
    parts = chunking.split(text)
    assert all(len(p) <= chunking.CHUNK_CHARS for p in parts) and len(parts) > 10
    assert "Lovelace" in " ".join(parts) and "".join(text.split()) == "".join("".join(parts).split())


def test_a_long_document_is_stored_as_passages(owner, agent):
    share(agent, long_text())
    with session_scope() as s:
        n = s.scalar(select(func.count()).select_from(DocumentChunk))
        assert n and n > 10
        assert [c.ordinal for c in s.scalars(select(DocumentChunk).order_by(DocumentChunk.ordinal))] == list(range(n))


def test_every_passage_is_read_not_just_the_first_twelve_thousand_characters(owner, agent, script):
    text = long_text()
    assert len(text) > 3 * MAX_INPUT_CHARS
    r = share(agent, text)
    assert r.status == "read" and len(script.calls) >= 2
    assert any("Lovelace" in c for c in script.calls)  # the fact is on page 6, past the first 12,000 characters
    with session_scope() as s:
        item = s.scalar(select(MemoryItem).where(MemoryItem.content == NEEDLE))
        assert item is not None and item.chunk_id is not None
        assert "Lovelace" in s.get(DocumentChunk, item.chunk_id).text  # the fact points at its passage


def test_a_fact_found_only_in_a_late_passage_is_recalled_and_cited_with_it(owner, agent):
    share(agent, long_text())
    found = asked("Lovelace Medal")
    kinds = {s.kind for s in found}
    assert "memory" in kinds and "chunk" in kinds
    passage = next(s for s in found if s.kind == "chunk")
    assert passage.uri.startswith("ava://chunk/") and "Lovelace" in passage.text and "cv.pdf" in passage.title
    cid = passage.uri.rsplit("/", 1)[1]
    got = owner.get(f"/app/passages/{cid}").json()
    assert "Lovelace" in got["text"] and got["document"]["title"] == "cv.pdf"


def test_offline_the_text_is_still_searchable(owner, agent, monkeypatch):
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: False)
    r = share(agent, long_text())
    assert r.status == "stored"
    assert any(s.kind == "chunk" and "Lovelace" in s.text for s in asked("Lovelace Medal"))


def test_a_removed_document_is_not_searchable(owner, agent):
    r = share(agent, long_text())
    owner.post(f"/app/intro/documents/{r.document_id}/remove")
    assert not [s for s in asked("Lovelace Medal") if s.kind == "chunk"]


def test_the_extraction_reserves_its_cost_for_each_window(owner, agent, script):
    share(agent, long_text())
    assert len(script.calls) >= 2  # each is a model call, and the client reserves against MAX_SPEND_USD before it


def test_a_window_that_cannot_be_read_leaves_the_document_to_be_retried(owner, agent, script, monkeypatch):
    real = agent.llm.responder
    n = {"i": 0}

    def flaky(purpose, system, messages, output_model):
        if purpose == "intro_facts":
            n["i"] += 1
            if n["i"] == 2:
                raise RuntimeError("cap")
        return real(purpose, system, messages, output_model)

    agent.llm.responder = flaky
    r = share(agent, long_text())
    assert r.status == "stored" and "couldn't read" in r.message
    with session_scope() as s:
        assert s.scalar(select(Document.status)) == "stored"


# --- the gate -------------------------------------------------------------------------------------


def test_nobody_but_the_owner_alone_can_get_a_passage(owner, agent, colleague):
    share(agent, long_text())
    with session_scope() as s:
        for aud in (
            Audience(person_ids={colleague.id}, min_trust=2),
            Audience(person_ids={colleague.id}, has_unknown=True, min_trust=0),
            Audience(person_ids=set(), has_unknown=True),
            Audience(person_ids={hired_owner(s).id, colleague.id}, min_trust=2),
            Audience(person_ids={hired_owner(s).id}, has_unknown=True, min_trust=2),
        ):
            assert [x for x in recall(s, aud, "Lovelace Medal") if x.kind == "chunk"] == [], aud


def test_the_passage_route_is_for_the_owner_only(owner, agent, browser_other=None):
    share(agent, long_text())
    with session_scope() as s:
        cid = s.scalar(select(DocumentChunk.id))
    assert owner.get(f"/app/passages/{cid}").status_code == 200
    assert owner.get("/app/passages/00000000-0000-0000-0000-000000000000").status_code == 404
    assert owner.get("/app/passages/not-a-uuid").status_code == 422
    other = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    assert other.get(f"/app/passages/{cid}").status_code == 401


def test_meaning_search_over_passages(owner, agent, monkeypatch):
    monkeypatch.setattr(FakeEmbedder, "concepts", {"lovelace": "prize", "trophy": "prize"})
    share(agent, "Awards. The Lovelace Medal, 2019.", title="awards.txt", kind="text")
    run_due()
    with session_scope() as s:
        assert s.scalar(text("SELECT count(*) FROM chunk_embedding")) > 0
    assert any(s.kind == "chunk" and "Lovelace" in s.text for s in asked("which trophy did I win"))
