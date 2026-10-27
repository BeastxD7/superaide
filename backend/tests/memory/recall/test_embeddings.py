"""Meaning search: facts are embedded in the background and found by paraphrase, behind the gate."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from superaide.brain import embed
from superaide.brain.embed import FakeEmbedder, LocalEmbedder, NoEmbedder, VoyageEmbedder, get_embedder
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.memory.audience import Audience
from superaide.memory.knowledge.store import remember_fact
from superaide.memory.recall import vectors
from superaide.memory.recall.retrieval import recall
from superaide.platform.config import Settings
from superaide.platform.db.models import Job, MemoryItem, SpendEntry
from superaide.platform.db.session import get_engine, session_scope

load()
TOKYO = "Jordan flies to Tokyo on Friday."


@pytest.fixture(autouse=True)
def concepts(monkeypatch):
    monkeypatch.setattr(FakeEmbedder, "concepts", {"tokyo": "japan", "japan": "japan"})


def _embedded(s) -> int:
    return s.scalar(text("SELECT count(*) FROM memory_embedding")) or 0


def owner_only(pid) -> Audience:
    return Audience(person_ids={pid}, min_trust=2)


def test_a_new_fact_is_embedded_by_a_background_job(manager):
    with session_scope() as s:
        remember_fact(s, owner=manager, content=TOKYO, category="work")
        assert s.scalar(select(Job.kind).where(Job.dedupe_key.like("embed:%"))) == vectors.JOB_KIND
        assert _embedded(s) == 0  # nothing in the request path
    run_due()
    with session_scope() as s:
        assert s.execute(text("SELECT model FROM memory_embedding")).scalars().all() == ["fake"]


def test_meaning_search_finds_a_paraphrase_that_shares_no_word(manager):
    with session_scope() as s:
        remember_fact(s, owner=manager, content=TOKYO, category="work")
        remember_fact(s, owner=manager, content="Jordan is vegetarian.", category="preference")
    run_due()
    with session_scope() as s:
        found = recall(s, owner_only(manager.id), "Japan trip")
        assert [f.text for f in found] == [TOKYO]


def test_meaning_search_never_returns_what_the_audience_may_not_see(manager, colleague):
    with session_scope() as s:
        s.add(MemoryItem(content="Priya flies to Tokyo for the offsite.", subject_person_ids=[colleague.id],
                         allowed_kind="persons", allowed_person_ids=[colleague.id], min_trust=2))  # fmt: skip
        remember_fact(s, owner=manager, content=TOKYO, category="work")
    with session_scope() as s:
        vectors.embed_missing(s)
    with session_scope() as s:
        assert _embedded(s) == 2
        assert [f.text for f in recall(s, owner_only(manager.id), "Japan trip")] == [TOKYO]
        assert [f.text for f in recall(s, owner_only(colleague.id), "Japan trip")] == [
            "Priya flies to Tokyo for the offsite."
        ]
        assert recall(s, Audience(person_ids={manager.id}, has_unknown=True), "Japan trip") == []  # a group


def test_another_ava_instances_vectors_are_never_used(manager):
    import uuid

    with session_scope() as s:
        item = remember_fact(s, owner=manager, content=TOKYO, category="work")
        assert item is not None
        vectors.embed_missing(s)
        s.execute(text("UPDATE memory_embedding SET agent_id = :a"), {"a": uuid.uuid4()})
    with session_scope() as s:
        assert recall(s, owner_only(manager.id), "Japan trip") == []


def test_without_pgvector_recall_is_keyword_only_and_nothing_fails(manager):
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE memory_embedding"))
        with session_scope() as s:
            remember_fact(s, owner=manager, content=TOKYO, category="work")
        run_due()
        with session_scope() as s:
            assert recall(s, owner_only(manager.id), "Japan trip") == []
            assert [f.text for f in recall(s, owner_only(manager.id), "Tokyo")] == [TOKYO]
    finally:
        vectors.ensure_vector_store(engine)


def test_the_backfill_embeds_facts_saved_before_embeddings_existed(manager):
    with session_scope() as s:
        for i in range(70):  # more than one batch
            s.add(MemoryItem(content=f"Fact number {i} about Tokyo", subject_person_ids=[manager.id],
                             allowed_kind="persons", allowed_person_ids=[manager.id], min_trust=2))  # fmt: skip
    with session_scope() as s:
        vectors.start(s)
    run_due()
    with session_scope() as s:
        assert _embedded(s) == 70


def test_with_no_embedder_nothing_is_queued(manager, monkeypatch):
    monkeypatch.setattr(vectors, "embedder", lambda: NoEmbedder())
    with session_scope() as s:
        remember_fact(s, owner=manager, content=TOKYO, category="work")
        assert s.scalar(select(func.count()).select_from(Job)) == 0


def test_a_local_model_still_loading_never_blocks_the_worker(manager, monkeypatch):
    local = LocalEmbedder("some/model", "")
    monkeypatch.setattr(local, "warm", lambda: None)
    monkeypatch.setattr(vectors, "embedder", lambda: local)
    with session_scope() as s:
        remember_fact(s, owner=manager, content=TOKYO, category="work")
    run_due()
    with session_scope() as s:
        assert _embedded(s) == 0
        assert s.scalar(select(Job.id).where(Job.dedupe_key.like("embed:wait:%"), Job.status == "queued"))


def test_the_default_is_local_and_voyage_needs_the_owners_opt_in():
    base = Settings(embedder="local", voyage_api_key="")
    assert isinstance(get_embedder(base), LocalEmbedder)
    assert isinstance(get_embedder(Settings(embedder="voyage", voyage_api_key="")), NoEmbedder)  # no key, no calls
    assert isinstance(get_embedder(Settings(embedder="voyage", voyage_api_key="k")), VoyageEmbedder)


def test_voyage_calls_are_on_the_ledger_and_stop_at_their_monthly_cap(monkeypatch):
    calls: list[dict] = []

    class Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {"total_tokens": 1000}}

    def post(url, headers, json, timeout):
        calls.append(json)
        return Resp()

    monkeypatch.setattr(embed.httpx, "post", post)
    v = VoyageEmbedder("k", "voyage-4-lite", monthly_cap_usd=0.00002)
    assert v.embed(["Jordan is vegetarian."], kind="document") == [[1.0, 0.0]]
    assert calls[0]["input_type"] == "document" and calls[0]["model"] == "voyage-4-lite"
    with session_scope() as s:
        entry = s.scalar(select(SpendEntry).where(SpendEntry.provider == "voyage"))
        assert entry.purpose == "embed" and entry.cost_usd == Decimal("0.00002")
    assert v.embed(["Jordan is vegetarian."], kind="document") is None  # the next would pass the cap
    assert len(calls) == 1
