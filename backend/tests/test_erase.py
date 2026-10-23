"""Real erasure: taking a source away deletes its text, passages, facts and vectors (issue #48)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.app.accounts import owner as hired_owner
from superaide.brain.llm import FakeLLM
from superaide.db.models import AuditLog, Contact, Document, DocumentChunk, Event, MemoryItem
from superaide.db.session import session_scope
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.knowledge.erase import erase_person, roll_off_third_party_messages, vacuum
from superaide.knowledge.extract import Extracted
from superaide.knowledge.facts import Fact, FactList
from superaide.knowledge.ingest import ingest
from superaide.knowledge.store import add_contact
from superaide.main import create_app

load()
PASSWORD = "correct horse battery"
PHRASE = "zanzibar-quokka-7731"
FACT = "Jordan collects zanzibar quokka stamps."


class Script:
    def __init__(self) -> None:
        self.facts: list[Fact] = [Fact(content=FACT, category="about", confidence=1)]

    def __call__(self, purpose, system, messages, output_model):
        return FactList(facts=self.facts) if purpose == "intro_facts" else "Noted."


@pytest.fixture
def script(agent, monkeypatch):
    m = Script()
    monkeypatch.setattr("superaide.factory.brain_ready", lambda settings: True)
    monkeypatch.setattr("superaide.factory.build_llm", lambda settings: FakeLLM(m))
    agent.llm = FakeLLM(m)
    return m


@pytest.fixture
def owner(agent, script):
    client = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    client.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD})
    client.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    return client


def share(agent, body: str, title: str = "stamps.pdf"):
    with session_scope() as s:
        pid = hired_owner(s).id
    return ingest(agent, pid, Extracted(text=body, title=title, kind="pdf", source=title))


def traces(phrase: str) -> dict[str, int]:
    """Every place in the database the phrase (or its vectors) could still be."""
    like = f"%{phrase}%"
    with session_scope() as s:
        q = lambda sql: s.scalar(text(sql), {"p": like}) or 0  # noqa: E731
        return {
            "document": q("SELECT count(*) FROM document WHERE text ILIKE :p OR title ILIKE :p OR source ILIKE :p"),
            "chunks": q("SELECT count(*) FROM document_chunk WHERE text ILIKE :p"),
            "facts": q("SELECT count(*) FROM memory_item WHERE content ILIKE :p"),
            "events": q("SELECT count(*) FROM event WHERE content ILIKE :p"),
            "audit": q("SELECT count(*) FROM audit_log WHERE detail::text ILIKE :p"),
        }


def vector_rows() -> int:
    with session_scope() as s:
        return (s.scalar(text("SELECT count(*) FROM chunk_embedding")) or 0) + (
            s.scalar(text("SELECT count(*) FROM memory_embedding")) or 0
        )


BODY = f"Stamps. The {PHRASE} collection is large.\n\nMore about stamps and quokkas."


def test_erase_leaves_no_trace_of_the_file_anywhere(owner, agent):
    r = share(agent, BODY)
    run_due()  # the vectors for its passages and facts
    assert vector_rows() > 0 and sum(traces(PHRASE).values()) > 0
    got = owner.post(f"/app/intro/documents/{r.document_id}/erase")
    assert got.status_code == 200 and got.json()["facts_deleted"] == 1
    assert traces(PHRASE) == {"document": 0, "chunks": 0, "facts": 0, "events": 0, "audit": 0}
    assert traces("stamps.pdf") == {"document": 0, "chunks": 0, "facts": 0, "events": 0, "audit": 0}
    assert vector_rows() == 0
    with session_scope() as s:
        assert s.scalar(select(Document)) is None and s.scalar(select(DocumentChunk)) is None
        assert s.scalar(select(MemoryItem).where(MemoryItem.content == FACT)) is None


def test_the_audit_line_has_counts_and_no_content(owner, agent):
    r = share(agent, BODY)
    owner.post(f"/app/intro/documents/{r.document_id}/erase")
    with session_scope() as s:
        line = s.scalar(select(AuditLog).where(AuditLog.action == "document_erased"))
        assert line is not None and line.detail["facts_deleted"] == 1
        assert PHRASE not in str(line.detail) and "stamps" not in str(line.detail).lower()


def test_erasing_one_of_two_sources_keeps_the_fact_and_its_other_source(owner, agent):
    a = share(agent, BODY, title="a.pdf")
    b = share(agent, BODY + "\n\nA second file saying the same.", title="b.pdf")
    with session_scope() as s:
        fact = s.scalar(select(MemoryItem).where(MemoryItem.content == FACT))
        assert fact is not None and len({fact.document_id, *fact.source_document_ids}) == 2
    got = owner.post(f"/app/intro/documents/{a.document_id}/erase").json()
    assert got["facts_deleted"] == 0 and got["facts_kept"] == 1
    with session_scope() as s:
        fact = s.scalar(select(MemoryItem).where(MemoryItem.content == FACT))
        assert fact is not None and fact.document_id == b.document_id and fact.source_document_ids == []
    assert owner.post(f"/app/intro/documents/{b.document_id}/erase").json()["facts_deleted"] == 1
    assert traces(PHRASE)["facts"] == 0


def test_a_fact_the_owner_also_said_in_chat_survives_the_erase(owner, agent):
    r = share(agent, BODY)
    with session_scope() as s:
        fact = s.scalar(select(MemoryItem).where(MemoryItem.content == FACT))
        fact.source_event_ids = [uuid.uuid4()]  # the owner confirmed it in a message
    got = owner.post(f"/app/intro/documents/{r.document_id}/erase").json()
    assert got["facts_deleted"] == 0 and got["facts_kept"] == 1
    assert traces(PHRASE)["document"] == 0 and traces(PHRASE)["chunks"] == 0


def test_a_hidden_document_can_still_be_erased_and_hiding_is_separate(owner, agent):
    r = share(agent, BODY)
    assert owner.post(f"/app/intro/documents/{r.document_id}/remove").status_code == 200  # hide
    assert traces(PHRASE)["document"] == 1  # the text is still there: that is what hiding is
    assert owner.post(f"/app/intro/documents/{r.document_id}/erase").status_code == 200
    assert traces(PHRASE)["document"] == 0


def test_erasing_what_was_retired_or_superseded_clears_pointers(owner, agent):
    r = share(agent, BODY)
    with session_scope() as s:
        fact = s.scalar(select(MemoryItem).where(MemoryItem.content == FACT))
        older = MemoryItem(content="Jordan collects coins.", subject_person_ids=fact.subject_person_ids)
        s.add(older)
        s.flush()
        older.superseded_by = fact.id
    assert owner.post(f"/app/intro/documents/{r.document_id}/erase").status_code == 200
    with session_scope() as s:
        assert (
            s.scalar(select(MemoryItem.superseded_by).where(MemoryItem.content == "Jordan collects coins.")) is None
        )


def test_erase_route_is_the_owners_alone(owner, agent):
    r = share(agent, BODY)
    path = f"/app/intro/documents/{r.document_id}/erase"
    other = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    assert other.post(path).status_code == 401
    assert owner.post(f"/app/intro/documents/{uuid.uuid4()}/erase").status_code == 404
    assert owner.post("/app/intro/documents/not-a-uuid/erase").status_code == 422
    assert owner.post(path).status_code == 200
    assert owner.post(path).status_code == 404


def test_vacuum_never_raises():
    vacuum()


# --- third parties --------------------------------------------------------------------------------


def test_a_third_partys_notes_and_messages_can_be_erased(owner, agent):
    with session_scope() as s:
        me = hired_owner(s)
        contact = add_contact(s, owner=me, name="Priya Tan", relationship="team", notes=f"likes {PHRASE}")
        pid, cid = contact.person_id, contact.id
        note = MemoryItem(content=f"Priya said {PHRASE}.", subject_person_ids=[pid], author_person_id=pid)
        s.add(note)
        from superaide.memory.store import get_or_create_context  # noqa: F401

        s.flush()
    with session_scope() as s:
        from superaide.db.models import Context

        ctx = Context(provider="slack", tenant_scope="T", external_id="D1", kind="dm", audience_class="mixed_external")
        s.add(ctx)
        s.flush()
        s.add(
            Event(
                context_id=ctx.id,
                direction="in",
                content=f"hello {PHRASE}",
                resolved_person_id=pid,
                audience_person_ids=[pid],
            )
        )
    assert sum(traces(PHRASE).values()) > 0
    got = owner.post(f"/app/people/{cid}/erase").json()
    assert got["notes_deleted"] == 1 and got["messages_emptied"] == 1
    assert traces(PHRASE) == {"document": 0, "chunks": 0, "facts": 0, "events": 0, "audit": 0}
    with session_scope() as s:
        assert s.get(Contact, cid).notes == ""
    assert owner.post(f"/app/people/{uuid.uuid4()}/erase").status_code == 404


def test_the_owner_cannot_be_erased_this_way(owner):
    with session_scope() as s:
        me = hired_owner(s)
        with pytest.raises(ValueError):
            erase_person(s, me, me.id)


def test_old_third_party_messages_roll_off_only_when_asked(owner):
    from datetime import UTC, datetime, timedelta

    from superaide.db.models import Context

    with session_scope() as s:
        me = hired_owner(s)
        other = add_contact(s, owner=me, name="Dana", relationship="other").person_id
        ctx = Context(provider="slack", tenant_scope="T", external_id="D2", kind="dm", audience_class="mixed_external")
        s.add(ctx)
        s.flush()
        old = datetime.now(UTC) - timedelta(days=100)
        for who, text_ in ((other, "old third party"), (me.id, "old owner message")):
            s.add(
                Event(
                    context_id=ctx.id,
                    direction="in",
                    content=text_,
                    resolved_person_id=who,
                    created_at=old,
                    audience_person_ids=[who],
                )
            )
        assert roll_off_third_party_messages(s, me, 0) == 0  # off by default
        assert roll_off_third_party_messages(s, me, 90) == 1
        kept = {e.content for e in s.scalars(select(Event).where(Event.context_id == ctx.id))}
        assert kept == {"", "old owner message"}  # the owner's own words live as long as the account
