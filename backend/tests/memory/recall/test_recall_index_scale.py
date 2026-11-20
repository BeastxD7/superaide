"""#647: the index at the size ADR 0014 plans for (10,000 rows in one instance): the plan of the one statement, the
timing of an exact vector scan, and the approximate-index job that takes over past a threshold."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import event, select, text

from superaide.brain.embed import FakeEmbedder
from superaide.memory.audience import Audience
from superaide.memory.models import MemoryItem
from superaide.memory.recall import index, index_policy
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import get_engine, session_scope
from superaide.platform.tenancy.context import scope_or_default

ROWS = 10_000
WORDS = ["orchid", "harbour", "invoice", "lantern", "quartz", "meadow", "ticket", "violin", "granite", "saffron"]
PUBLIC = Audience(person_ids=set(), has_unknown=True, min_trust=0, all_internal=False)  # a stranger: public rows


@pytest.fixture
def hnsw_cleanup() -> Iterator[None]:
    yield
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"DROP INDEX IF EXISTS {index.hnsw_index_name('fake')}"))


@pytest.fixture
def big() -> Iterator[None]:
    """10,000 public notes, indexed, each with a random 64-dimension vector for the model "fake"."""
    with session_scope() as s:
        sc = scope_or_default()
        s.execute(
            text(
                """
                INSERT INTO memory_item (id, tenant_id, agent_id, created_at, content, subject_person_ids,
                    source_event_ids, allowed_kind, allowed_person_ids, min_trust, valid_from, category, source,
                    conflict_status)
                SELECT gen_random_uuid(), :t, :a, now() - (i || ' minutes')::interval,
                    'note ' || i || ' about ' || (ARRAY[:w])[1 + i % :n] || ' and ' || (ARRAY[:w])[1 + (i * 7) % :n],
                    '{}', '{}', 'public', '{}', 0, now() - interval '1 day', 'other', 'chat', 'none'
                FROM generate_series(1, :rows) AS i
                """.replace(":w", ",".join(f"'{w}'" for w in WORDS))
            ),
            {"t": sc.tenant_id, "a": sc.agent_id, "n": len(WORDS), "rows": ROWS},
        )
        index.sync(s)
        s.execute(
            text(
                """
                INSERT INTO recall_embedding (doc_id, model, tenant_id, agent_id, embedding)
                SELECT id, 'fake', tenant_id, agent_id,
                    ('[' || (SELECT string_agg(random()::text, ',')
                             FROM generate_series(1, 64) WHERE d.id IS NOT NULL) || ']')::vector
                FROM recall_doc d
                """
            )
        )
        s.execute(text("ANALYZE recall_doc"))
        s.execute(text("ANALYZE recall_embedding"))
    yield


def _plan(session, statement: str, params: Any) -> dict[str, Any]:
    session.execute(
        text("SET LOCAL enable_seqscan = off")
    )  # prove the indexes can serve it, whatever the planner prefers
    row = session.connection().exec_driver_sql("EXPLAIN (ANALYZE, FORMAT JSON) " + statement, params).scalar()
    return row[0] if isinstance(row, list) else json.loads(row)[0]


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _captured(session, query: str) -> tuple[str, Any]:
    """The one SELECT `index.search` sends (the statement itself, with its parameters)."""
    seen: list[tuple[str, Any]] = []
    engine = session.get_bind()

    @event.listens_for(engine, "before_cursor_execute")
    def grab(conn, cursor, statement, parameters, context, executemany):
        if "UNION ALL" in statement:
            seen.append((statement, parameters))

    try:
        index.search(session, PUBLIC, query, emb=FakeEmbedder())
    finally:
        event.remove(engine, "before_cursor_execute", grab)
    assert len(seen) == 1, "recall is one statement"
    return seen[0]


def test_the_one_statement_uses_the_text_index_and_the_instance_scope_and_the_vector_arm(big):
    with session_scope() as s:
        statement, params = _captured(s, "orchid harbour violin")
        plan = _plan(s, statement, params)
        nodes = list(_nodes(plan["Plan"]))
        used = {n.get("Index Name") for n in nodes if n.get("Index Name")}
        assert "ix_recall_doc_tsv" in used  # the full-text arm reads the GIN index
        assert "ix_recall_embedding_agent_model" in used  # the vector arm is scoped to the instance by its index
        reads = [n for n in nodes if n.get("Relation Name") in ("recall_doc", "recall_embedding")]
        assert {n["Relation Name"] for n in reads} == {"recall_doc", "recall_embedding"}
        for n in reads:
            if n["Node Type"] == "Bitmap Index Scan" or n.get("Index Name") in ("recall_doc_pkey",):
                continue  # the index half of a bitmap scan, and the probe by id of a row an arm already chose
            assert "agent_id" in json.dumps(n), n  # every other read carries the instance in its condition or filter
        assert plan["Execution Time"] < 1500  # even with sequential scans forbidden


def test_an_exact_vector_scan_over_ten_thousand_rows_is_fast_enough(big):
    emb = FakeEmbedder()
    with session_scope() as s:
        t0 = time.perf_counter()
        runs = 5
        for _ in range(runs):
            hits = index.search(s, PUBLIC, "orchid harbour violin", emb=emb)
        per_query = (time.perf_counter() - t0) / runs
    assert hits
    assert per_query < 0.5, per_query  # report 6.4: 8 ms for 5,000 rows on a laptop; this is a generous bound for CI


def test_every_hit_at_scale_is_inside_the_gate(big):
    with session_scope() as s:
        hits = index.search(s, PUBLIC, "orchid", limit=20, emb=FakeEmbedder())
        assert hits and all(h.kind == "note" and h.taint == "owner" for h in hits)
        # a private note added after indexing is invisible to a stranger even before the sync
        s.add(MemoryItem(content="orchid secret", allowed_kind="persons", allowed_person_ids=[]))
        s.flush()
        index.sync(s)
        assert "orchid secret" not in {
            h.text for h in index.search(s, PUBLIC, "orchid secret", limit=50, emb=FakeEmbedder())
        }


# --- the approximate index ----------------------------------------------------------------------


def test_below_the_threshold_search_stays_exact_and_no_index_is_built(big, hnsw_cleanup):
    with session_scope() as s:
        assert index_policy.apply(s, threshold=ROWS + 1) == []
        assert index.hnsw_dimension(s, "fake") is None
        assert s.scalar(select(AuditLog.id).where(AuditLog.action == "approximate_index")) is None


def test_past_the_threshold_a_partial_hnsw_index_is_built_on_the_models_dimension_and_recorded(big, hnsw_cleanup):
    emb = FakeEmbedder()
    with session_scope() as s:
        exact = [h.ref_id for h in index.search(s, PUBLIC, "orchid harbour", limit=5, emb=emb)]
        assert index_policy.apply(s, threshold=ROWS) == ["fake"]
        assert index_policy.apply(s, threshold=ROWS) == []  # once: the index is there
        assert index.hnsw_dimension(s, "fake") == 64
        defn = s.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": index.hnsw_index_name("fake")}
        )
        assert "hnsw" in defn and "::vector(64)" in defn and "WHERE" in defn  # partial, on a cast to the dimension
        note = s.scalar(select(AuditLog).where(AuditLog.action == "approximate_index"))
        assert note.detail["rows"] == ROWS and note.detail["threshold"] == ROWS and note.detail["built"] is True
        assert note.detail["p95_ms"] >= 0 and note.detail["model"] == "fake"
        # the search still answers, from the same statement, with the same gate
        approx = index.search(s, PUBLIC, "orchid harbour", limit=5, emb=emb)
        assert [h.ref_id for h in approx] and {h.kind for h in approx} == {"note"}
        assert set(exact) & {h.ref_id for h in approx}
        statement, params = _captured(s, "orchid harbour")
        used = {n.get("Index Name") for n in _nodes(_plan(s, statement, params)["Plan"])}
        assert index.hnsw_index_name("fake") in used  # the query casts to the same expression, so the index is used


def test_a_model_over_two_thousand_dimensions_keeps_the_exact_scan(big, hnsw_cleanup):
    with session_scope() as s:
        assert index_policy.ensure_index(s, "fake", index_policy.MAX_DIM + 1) is False
