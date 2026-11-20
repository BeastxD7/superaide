"""#647: the derived recall index: built from the sources, gated in the one statement, compared with today's recall.

The golden world of #648 (Jordan Whitfield's notes, Zeeshan Khan's and Meera Iyer's messages, Priya Tan's DM, a file) is
the data. The gate tests ask, for every kind and every audience, whether the index shows exactly what today's
per-source gates show; the shadow tests ask whether its top five matches today's for note and passage queries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text, update

from superaide.brain.embed import FakeEmbedder
from superaide.evals import retrieval
from superaide.evals.checks import _audience
from superaide.jobs.handlers import load
from superaide.jobs.models import Job
from superaide.jobs.queue import run_due
from superaide.memory.audience import Audience, memory_filter
from superaide.memory.knowledge.store import remember_fact
from superaide.memory.models import Document, DocumentChunk, Event, MemoryItem, RecallDoc
from superaide.memory.recall import index, lexicon
from superaide.memory.recall.retrieval import (
    _message_gate,
    chunk_filter,
    recall_chunks,
    recall_memories,
    visitor_rows,
)
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance
from superaide.reception.models import ReceptionMessage

AUDIENCES = ("owner", "colleague", "stranger", "group")


class MoneyEmbedder(FakeEmbedder):
    """The fake embedder with a concept map: bill, payment and invoice are one idea, as a real model would put them."""

    name = "fake-money"
    concepts = {w: "money" for w in ("bill", "payment", "pending", "invoice", "overdue", "settled", "unpaid", "owed")}


class OtherEmbedder(MoneyEmbedder):
    name = "fake-other"


class NoVectors(FakeEmbedder):
    name = "fake-none"

    def ready(self) -> bool:
        return False


@pytest.fixture
def world():
    w = retrieval.build(retrieval.load())
    w.sources = {v: uuid.UUID(k) for k, v in w.names.items()}  # key -> the note, event or passage it names
    with session_scope() as s:  # a visitor message is indexed by its own row: name it like its inbound event
        for r in s.scalars(select(ReceptionMessage)):
            w.names[str(r.id)] = w.names[str(r.inbound_event_id)]
    return w


@pytest.fixture
def keys(world):
    return world.names


@pytest.fixture
def by(world):
    return world.sources


def _old_visible(s, aud: Audience, rooms: bool) -> set[tuple[str, uuid.UUID]]:
    """What today's per-source gates show this audience: the oracle the index must equal."""
    out: set[tuple[str, uuid.UUID]] = set()
    out |= {("note", i) for i in s.scalars(select(MemoryItem.id).where(memory_filter(aud)))}
    if not aud.has_unknown and aud.person_ids:
        gate = _message_gate(aud) & ~Event.meta.has_key("ambient")
        if rooms:
            gate = gate | Event.meta.has_key("ambient")
        out |= {
            ("message", i)
            for i in s.scalars(
                select(Event.id).where(gate, Event.content != "", Event.direction == "in", Event.erased_at.is_(None))
            )
        }
    out |= {("visitor_message", r.id) for r in visitor_rows(s, aud, limit=1000)}
    out |= {
        ("chunk", i)
        for i in s.scalars(
            select(DocumentChunk.id).join(Document, Document.id == DocumentChunk.document_id).where(chunk_filter(aud))
        )
    }
    return out


def _names(world, refs) -> set[str]:
    return {world.names.get(str(r), "?") for _, r in refs}


# --- building -----------------------------------------------------------------------------------


def test_every_kind_is_indexed_from_its_source_with_the_taint_of_who_wrote_it(world):
    with session_scope() as s:
        counts = index.sync(s)
        assert counts["note"] == s.scalar(select(func.count()).select_from(MemoryItem))
        assert counts["visitor_message"] == 4 and counts["chunk"] == 1
        taint = {(d.kind, world.names.get(str(d.ref_id))): d.taint for d in s.scalars(select(RecallDoc))}
    assert taint[("visitor_message", "v_meera_invoice")] == "stranger"
    assert taint[("chunk", "f_drill")] == "document"
    assert taint[("note", "n_meera_invoice")] == "owner"
    assert taint[("message", "c_signoff")] == "known"  # the colleague's own DM: a known person's words


def test_rebuilding_the_index_from_the_sources_yields_the_same_rows(world):
    def snapshot(s):
        cols = [c for c in RecallDoc.__table__.c if c.name not in ("id", "created_at", "tsv")]
        return sorted(tuple(str(v) for v in row) for row in s.execute(select(*cols)).all())

    with session_scope() as s:
        index.sync(s)
        before = snapshot(s)
        again = index.sync(s)
        assert set(again.values()) == {0}, again  # idempotent: nothing is added, changed or retired the second time
        assert snapshot(s) == before
        s.execute(delete(RecallDoc))  # the table is derived: lose it all and rebuild
        index.sync(s)
        assert snapshot(s) == before


# --- the gate: every kind, every audience, today's gates as the oracle ---------------------------


@pytest.mark.parametrize("rooms", [False, True])
@pytest.mark.parametrize("who", AUDIENCES)
def test_the_index_shows_each_audience_exactly_what_todays_gates_show(world, who, rooms):
    aud = _audience(who)
    with session_scope() as s:
        index.sync(s)
        assert index.visible_refs(s, aud, rooms=rooms) == _old_visible(s, aud, rooms)


def test_a_colleague_a_stranger_and_a_group_never_see_a_visitor_message_or_a_passage(world):
    for who in ("colleague", "stranger", "group"):
        aud = _audience(who)
        with session_scope() as s:
            index.sync(s)
            assert not {k for k, _ in index.visible_refs(s, aud, rooms=True)} & {"visitor_message", "chunk"}
            hits = index.search(s, aud, "invoice contract signature Kreo fire drill", rooms=True, emb=NoVectors())
            assert not [h for h in hits if h.kind in ("visitor_message", "chunk")]
    with session_scope() as s:  # a stranger and a group see public notes only
        assert {k for k, _ in index.visible_refs(s, _audience("stranger"))} <= {"note"}
        assert _names(world, index.visible_refs(s, _audience("group"))) == {"n_office_hours"}


def test_the_colleague_reads_her_own_dm_and_what_the_org_may_see_and_nothing_else(world):
    with session_scope() as s:
        index.sync(s)
        assert _names(world, index.visible_refs(s, _audience("colleague"))) == {
            "n_office_hours",
            "n_review",
            "c_signoff",
        }


def test_a_second_owner_sees_none_of_the_first_owners_rows(world):
    other = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    owner = _audience("owner")
    with session_scope() as s:
        index.sync(s)
        assert s.scalar(select(func.count()).select_from(RecallDoc)) > 0
    with use_instance(other), session_scope() as s:
        assert s.scalar(select(func.count()).select_from(RecallDoc)) == 0
        # even given the first owner's own audience, the statement is scoped to this instance
        assert index.search(s, owner, "invoice", rooms=True, emb=NoVectors()) == []
        assert index.visible_refs(s, owner, rooms=True) == set()


# --- retired, expired, erased, changed -----------------------------------------------------------


def test_an_erased_or_retired_or_expired_source_is_never_returned_even_before_the_next_sync(world, by):
    aud = _audience("owner")
    now = datetime.now(UTC)
    colleague = _audience("colleague")
    gone = {"n_meera_invoice", "n_meera_hours", "v_meera_invoice", "f_drill"}
    with session_scope() as s:
        index.sync(s)
        before = _names(world, index.visible_refs(s, aud, rooms=True))
        assert gone <= before
        assert "c_signoff" in _names(world, index.visible_refs(s, colleague))
        note = s.get(MemoryItem, by["n_meera_invoice"])
        note.erased_at = now  # erased
        s.get(MemoryItem, by["n_meera_hours"]).valid_to = now - timedelta(days=1)  # expired
        s.get(Event, by["c_signoff"]).erased_at = now  # a message erased
        s.execute(
            update(ReceptionMessage)
            .where(ReceptionMessage.inbound_event_id == by["v_meera_invoice"])
            .values(status="spam")
        )  # a visitor message marked spam
        s.scalar(select(Document)).removed_at = now  # a file removed
        s.flush()
        assert _names(world, index.visible_refs(s, aud, rooms=True)) == before - gone
        found = index.search(s, aud, "Meera invoice", rooms=True, emb=NoVectors())
        assert note.id not in {h.ref_id for h in found}
        assert "c_signoff" not in _names(world, index.visible_refs(s, colleague))
        assert not index.search(s, colleague, "invoice sign-off", emb=NoVectors())
        # the next sync retires the rows (soft: they stay), and a source that comes back is live again
        index.sync(s)
        retired = s.scalar(select(func.count()).select_from(RecallDoc).where(RecallDoc.retired_at.is_not(None)))
        assert retired >= 4
        note.erased_at = None
        index.sync(s)
        assert ("note", note.id) in index.visible_refs(s, aud, rooms=True)


def test_a_note_made_more_private_is_hidden_at_once_and_stays_hidden_after_the_sync(world, by):
    """The gate columns are a copy. If the source's audience changed since indexing, the row is hidden, not served
    under the old audience (the lag fails closed)."""
    colleague = _audience("colleague")
    with session_scope() as s:
        index.sync(s)
        note = s.get(MemoryItem, by["n_review"])
        assert ("note", note.id) in index.visible_refs(s, colleague)
        note.allowed_kind = "persons"  # the owner made it private
        s.flush()
        assert ("note", note.id) not in index.visible_refs(s, colleague)  # before the sync
        index.sync(s)
        assert ("note", note.id) not in index.visible_refs(s, colleague)  # after it


# --- shadow: the new path against today's ------------------------------------------------------


def test_shadow_note_and_passage_queries_return_what_todays_recall_returns(world, keys):
    """For every owner query of the golden set, the keys of today's top five notes and passages are in the index's top
    five of those kinds, or the index found every expected key (better by the recorded metric)."""
    golden = retrieval.load()
    aud = _audience("owner")
    compared = 0
    with session_scope() as s:
        index.sync(s)
        for q in golden.queries:
            if q.as_ != "owner" or not lexicon.content_words(q.say) or q.category == "privacy_negative":
                continue
            old = [
                keys.get(src.uri.rsplit("/", 1)[-1], "?")
                for src in (recall_memories(s, aud, q.say, limit=5) + recall_chunks(s, aud, q.say, limit=5))
            ]
            new = [
                keys.get(str(h.ref_id), "?")
                for h in index.search(s, aud, q.say, kinds=("note", "chunk"), limit=10, emb=NoVectors())
            ]
            expected = [k for k in q.expect if k.startswith(("n_", "f_"))]
            if not old and not new:
                continue
            compared += 1
            old_found = {k for k in old if k in expected}
            new_found = {k for k in new[:5] if k in expected}
            assert old_found <= new_found or new_found == set(expected), (q.id, old, new)
    assert compared >= 3


# --- meaning search for messages and visitor messages ------------------------------------------


def test_bill_payment_pending_finds_the_invoice_visitor_message_by_meaning(world, keys):
    aud = _audience("owner")
    emb = MoneyEmbedder()
    with session_scope() as s:
        index.sync(s)
        keyword = index.search(s, aud, "bill payment pending", rooms=True, emb=NoVectors())
        assert not [h for h in keyword if h.kind == "visitor_message"]  # no word of the question is in the message
        assert index.embed_missing(s, emb) > 0
        hits = index.search(s, aud, "bill payment pending", rooms=True, emb=emb)
        found = [keys.get(str(h.ref_id)) for h in hits if h.kind == "visitor_message"]
        assert "v_meera_invoice" in found
        assert "v_meera_coffee" not in found


def test_taint_comes_from_the_column_not_from_a_title(world):
    aud = _audience("owner")
    with session_scope() as s:
        index.sync(s)
        mine = index.search(s, aud, "office hours weekdays", kinds=("note",), emb=NoVectors())
        assert mine and not index.tainted(mine)
        strangers = index.search(s, aud, "invoice", kinds=("visitor_message",), rooms=True, emb=NoVectors())
        assert strangers and index.tainted(strangers)
        passages = index.search(s, aud, "fire drill car park", kinds=("chunk",), emb=NoVectors())
        assert passages and index.tainted(passages)


# --- switching the embedder ---------------------------------------------------------------------


def test_switching_embedder_re_embeds_in_the_background_and_search_stays_keyword_only_meanwhile(world):
    aud = _audience("owner")
    first, second = MoneyEmbedder(), OtherEmbedder()
    with session_scope() as s:
        index.sync(s)
        index.embed_missing(s, first)
        assert index.search(s, aud, "bill payment pending", rooms=True, emb=first)
        # the new model has no vectors yet: no error, and what keywords find is still found
        assert index.search(s, aud, "invoice", rooms=True, emb=second)
        meaning = index.search(s, aud, "bill payment pending", rooms=True, emb=second)
        assert not [h for h in meaning if h.kind == "visitor_message"]
        assert index.embed_missing(s, second) > 0
        assert set(s.execute(text("SELECT DISTINCT model FROM recall_embedding")).scalars()) == {
            "fake-money",
            "fake-other",
        }
        meaning = index.search(s, aud, "bill payment pending", rooms=True, emb=second)
        assert [h for h in meaning if h.kind == "visitor_message"]


def test_a_changed_row_loses_its_vectors_so_they_are_re_embedded(world, by):
    with session_scope() as s:
        index.sync(s)
        index.embed_missing(s, MoneyEmbedder())
        note = s.get(MemoryItem, by["n_meera_hours"])
        note.content = "Meera prefers to be called after 6 pm."
        s.flush()
        index.sync(s)
        doc = s.scalar(select(RecallDoc).where(RecallDoc.ref_id == note.id))
        assert s.scalar(text("SELECT count(*) FROM recall_embedding WHERE doc_id = :d"), {"d": doc.id}) == 0
        assert index.embed_missing(s, MoneyEmbedder()) == 1


def test_the_embedding_job_keeps_the_index_in_step_and_queues_nothing_more(manager):
    load()
    with session_scope() as s:
        remember_fact(s, owner=manager, content="Jordan flies to Tokyo on Friday.", category="work")
        assert s.scalar(select(func.count()).select_from(RecallDoc)) == 0  # nothing in the request path
    run_due()
    with session_scope() as s:
        docs = list(s.scalars(select(RecallDoc)))
        assert [(d.kind, d.text) for d in docs] == [("note", "Jordan flies to Tokyo on Friday.")]
        assert s.execute(text("SELECT model FROM recall_embedding")).scalars().all() == ["fake"]
        assert s.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")) == 0
