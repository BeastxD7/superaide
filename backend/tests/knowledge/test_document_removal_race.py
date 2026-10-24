"""A document the owner removes while Ava is still reading it must not leave live facts behind (issue #87)."""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import select

from superaide.knowledge import ingest as ingest_module
from superaide.knowledge import store
from superaide.knowledge.extract import Extracted
from superaide.knowledge.facts import Fact
from superaide.knowledge.ingest import ingest, read_documents
from superaide.platform.db.models import Document, MemoryItem, Person
from superaide.platform.db.session import session_scope

FACTS = [Fact(content="Jordan leads applied AI at Larkfield.", category="work", confidence=1)]


@pytest.fixture
def extracted() -> Extracted:
    return Extracted(text="Jordan Whitfield, applied AI lead.", title="cv.txt", kind="text", source="cv.txt")


def remove_documents() -> None:
    with session_scope() as s:
        for doc in s.scalars(select(Document)):
            store.retire_document(s, doc)


def live_facts() -> list[MemoryItem]:
    with session_scope() as s:
        return list(
            s.scalars(select(MemoryItem).where(MemoryItem.document_id.is_not(None), MemoryItem.valid_to.is_(None)))
        )


def test_removing_a_document_while_the_model_reads_it_keeps_no_facts(agent, manager, extracted, monkeypatch):
    def reads_then_owner_removes_it(*a, **k):
        remove_documents()  # the owner clicks remove during the model call
        return [(f, None) for f in FACTS], True

    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)  # a model is connected
    monkeypatch.setattr(ingest_module, "extract_from_chunks", reads_then_owner_removes_it)
    result = ingest(agent, manager.id, extracted)
    assert result.facts == 0
    assert live_facts() == []
    with session_scope() as s:
        assert s.scalars(select(MemoryItem).where(MemoryItem.document_id.is_not(None))).all() == []


def test_removing_a_waiting_document_while_the_model_reads_it_keeps_no_facts(agent, manager, extracted, monkeypatch):
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: False)
    ingest(agent, manager.id, extracted)  # kept, not read
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)

    def reads_then_owner_removes_it(*a, **k):
        remove_documents()
        return [(f, None) for f in FACTS], True

    monkeypatch.setattr(ingest_module, "extract_from_chunks", reads_then_owner_removes_it)
    read_documents(agent, manager.id)
    assert live_facts() == []


def test_a_removal_that_lands_while_facts_are_being_saved_waits_and_retires_them(
    agent, manager, extracted, monkeypatch
):
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)  # a model is connected
    monkeypatch.setattr(ingest_module, "extract_from_chunks", lambda *a, **k: ([(f, None) for f in FACTS], True))
    saving = threading.Event()
    real = ingest_module.remember_fact

    def slow_remember(*a, **k):
        item = real(*a, **k)
        saving.set()
        time.sleep(0.4)  # the facts are written but not yet committed
        return item

    monkeypatch.setattr(ingest_module, "remember_fact", slow_remember)
    worker = threading.Thread(target=ingest, args=(agent, manager.id, extracted))
    worker.start()
    assert saving.wait(5)
    remove_documents()  # must wait for the save to finish, then retire what it wrote
    worker.join()
    assert live_facts() == []
    with session_scope() as s:
        assert s.scalar(select(Document.removed_at)) is not None
        assert s.get(Person, manager.id) is not None
