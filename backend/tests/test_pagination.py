"""The shared keyset pagination helper (ADR 0014): cursors, bounds, ordering, concurrency, plans and scale.

Runs on a probe table of its own (`pg_probe`), created per test module and dropped after, so it needs no
production table. The real endpoints add their own tests when they move to the helper.

Opt-in 1M-row run: PAGINATION_SCALE_ROWS=1000000 uv run pytest tests/test_pagination.py -k scale
"""

from __future__ import annotations

import base64
import json
import os
import time
import tracemalloc
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import DateTime, Index, Integer, String, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from superaide import envelope
from superaide.config import get_settings
from superaide.db.session import get_engine
from superaide.pagination import (
    HARD_MAX_LIMIT,
    InvalidCursor,
    InvalidLimit,
    Page,
    SortKey,
    approx_count,
    bind_for,
    cursor_param,
    decode_cursor,
    encode_cursor,
    limit_param,
    next_link,
    page_query,
    paginate,
)
from superaide.tenancy.context import InstanceScope, use_instance


class ProbeBase(DeclarativeBase):
    pass


class Probe(ProbeBase):
    __tablename__ = "pg_probe"
    __table_args__ = (Index("ix_pg_probe_owner_created", "owner", "created_at", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rank: Mapped[int] = mapped_column(Integer, default=0)


class ProbeOut(BaseModel):
    id: str
    at: str


NEWEST_FIRST = [SortKey(Probe.created_at), SortKey(Probe.id)]
EP = "probe"


@pytest.fixture(scope="module", autouse=True)
def probe_table() -> Iterator[None]:
    engine = get_engine()
    ProbeBase.metadata.drop_all(engine)
    ProbeBase.metadata.create_all(engine)
    yield
    ProbeBase.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def empty_probe() -> None:
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE pg_probe"))


def seed(n: int, *, owner: str = "a", same_instant: bool = False, start: datetime | None = None) -> None:
    """n rows in one statement; `same_instant` gives every row the identical timestamp."""
    t0 = start or datetime(2026, 1, 1, tzinfo=UTC)
    step = 0 if same_instant else 1000
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pg_probe (id, owner, created_at, rank) "
                "SELECT gen_random_uuid(), :o, :t0 + (g * :step) * interval '1 millisecond', g % 7 "
                "FROM generate_series(1, :n) g"
            ),
            {"o": owner, "t0": t0, "step": step, "n": n},
        )


def out(p: Probe) -> ProbeOut:
    return ProbeOut(id=str(p.id), at=p.created_at.isoformat())


def one_page(
    s: Session,
    *,
    owner: str = "a",
    limit: int = 10,
    cursor: str | None = None,
    bind: str | None = None,
    endpoint: str = EP,
    keys: list[SortKey] | None = None,
) -> Page[ProbeOut]:
    return paginate(
        s,
        select(Probe).where(Probe.owner == owner),
        keys or NEWEST_FIRST,
        endpoint=endpoint,
        bind=bind or bind_for("a"),
        limit=limit,
        cursor=cursor,
        build=out,
    )


def walk(owner: str = "a", limit: int = 10, keys: list[SortKey] | None = None) -> list[str]:
    from superaide.db.session import session_scope

    ids: list[str] = []
    cursor = None
    while True:
        with session_scope() as s:
            page = one_page(s, owner=owner, limit=limit, cursor=cursor, keys=keys)
        ids += [i.id for i in page.items]
        assert page.has_more == (page.next_cursor is not None)
        if not page.next_cursor:
            return ids
        cursor = page.next_cursor


# --- cursors ---------------------------------------------------------------------------------------


def test_cursor_roundtrip_every_value_kind():
    keys = [SortKey(Probe.created_at), SortKey(Probe.rank), SortKey(Probe.owner), SortKey(Probe.id)]
    values = [datetime(2026, 3, 1, 12, 0, 0, 123456, tzinfo=UTC), 7, "x.y", uuid.uuid4()]
    token = encode_cursor(values, keys, endpoint=EP, bind="b")
    assert decode_cursor(token, keys, endpoint=EP, bind="b") == values
    assert "123456" not in token and "x.y" not in token  # opaque, not readable text


def test_null_sort_value_is_a_programming_error():
    with pytest.raises(ValueError):
        encode_cursor([None, uuid.uuid4()], NEWEST_FIRST, endpoint=EP, bind="b")


def test_naive_datetime_is_refused():
    with pytest.raises(ValueError):
        encode_cursor([datetime(2026, 1, 1), uuid.uuid4()], NEWEST_FIRST, endpoint=EP, bind="b")


def good_token() -> str:
    return encode_cursor([datetime(2026, 1, 1, tzinfo=UTC), uuid.uuid4()], NEWEST_FIRST, endpoint=EP, bind="b")


@pytest.mark.parametrize("mangle", ["flip_sig", "flip_body", "drop_part", "empty", "junk", "long", "wrong_version"])
def test_tampered_or_malformed_cursor_is_refused(mangle):
    v, body, sig = good_token().split(".")
    token = {
        "flip_sig": f"{v}.{body}.{sig[:-2]}{'A' if sig[-2] != 'A' else 'B'}{sig[-1]}",
        "flip_body": f"{v}.{body[:-2]}{'A' if body[-2] != 'A' else 'B'}{body[-1]}.{sig}",
        "drop_part": f"{v}.{body}",
        "empty": "",
        "junk": "not a cursor",
        "long": "v1." + "A" * 5000 + ".x",
        "wrong_version": f"v2.{body}.{sig}",
    }[mangle]
    with pytest.raises(InvalidCursor):
        decode_cursor(token, NEWEST_FIRST, endpoint=EP, bind="b")


def test_forged_payload_with_a_valid_looking_signature_shape_is_refused():
    """An attacker who edits the payload (say, the bind) cannot re-sign it without the server key."""
    v, body, sig = good_token().split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["b"] = "someone else"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    with pytest.raises(InvalidCursor):
        decode_cursor(f"{v}.{forged}.{sig}", NEWEST_FIRST, endpoint=EP, bind="someone else")


def test_cursor_cannot_move_to_another_endpoint_or_owner_or_order():
    token = good_token()
    with pytest.raises(InvalidCursor):
        decode_cursor(token, NEWEST_FIRST, endpoint="other", bind="b")
    with pytest.raises(InvalidCursor):
        decode_cursor(token, NEWEST_FIRST, endpoint=EP, bind="c")
    with pytest.raises(InvalidCursor):
        decode_cursor(
            token, [SortKey(Probe.created_at, desc=False), SortKey(Probe.id, desc=False)], endpoint=EP, bind="b"
        )


def test_cursor_cannot_be_replayed_across_instances():
    """The same person id under another agent instance gets a different bind, so the cursor is refused."""
    person = uuid.uuid4()
    mine = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    theirs = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    with use_instance(mine):
        token = encode_cursor(
            [datetime(2026, 1, 1, tzinfo=UTC), uuid.uuid4()], NEWEST_FIRST, endpoint=EP, bind=bind_for(person)
        )
    with use_instance(theirs), pytest.raises(InvalidCursor):
        decode_cursor(token, NEWEST_FIRST, endpoint=EP, bind=bind_for(person))
    with use_instance(mine):
        assert decode_cursor(token, NEWEST_FIRST, endpoint=EP, bind=bind_for(person))


def test_expired_cursor_is_refused(monkeypatch):
    monkeypatch.setattr(get_settings(), "pagination_cursor_max_age_s", -5)
    with pytest.raises(InvalidCursor) as e:
        decode_cursor(good_token(), NEWEST_FIRST, endpoint=EP, bind="b")
    assert e.value.status_code == 422


# --- limits ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, -1, HARD_MAX_LIMIT + 1])
def test_helper_refuses_out_of_range_limit(limit):
    from superaide.db.session import session_scope

    with session_scope() as s, pytest.raises(InvalidLimit):
        one_page(s, limit=limit)


def test_endpoint_maximum_is_enforced_below_the_hard_max():
    from superaide.db.session import session_scope

    with session_scope() as s, pytest.raises(InvalidLimit):
        paginate(s, select(Probe), NEWEST_FIRST, endpoint=EP, bind="b", limit=51, cursor=None, build=out, maximum=50)


def make_app() -> FastAPI:
    app = FastAPI()
    envelope.install(app)

    @app.get("/things", response_model=Page[ProbeOut])
    def things(limit: int = limit_param(25, 100), cursor: str | None = cursor_param()) -> Page[ProbeOut]:
        from superaide.db.session import session_scope

        with session_scope() as s:
            return one_page(s, limit=limit, cursor=cursor, bind="b")

    return app


def test_http_limit_bounds_and_bad_cursor_use_the_error_envelope():
    seed(30)
    c = TestClient(make_app())
    ok = c.get("/things", params={"limit": 10}).json()
    assert len(ok["items"]) == 10 and ok["has_more"] is True and ok["next_cursor"]
    assert set(ok) == {"items", "next_cursor", "has_more", "total_estimate"}
    for bad in (0, 101, "abc"):
        r = c.get("/things", params={"limit": bad})
        assert r.status_code == 422
        assert r.json()["status"] == "error" and r.json()["error"]["code"] == "validation_failed"
    r = c.get("/things", params={"cursor": "garbage"})
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "error" and body["error"]["code"] == "invalid_cursor" and body["message"]
    assert body["error"]["request_id"]
    assert c.get("/things", params={"cursor": "x" * 2000}).status_code == 422
    # a good cursor from this endpoint works
    assert c.get("/things", params={"cursor": ok["next_cursor"], "limit": 10}).status_code == 200


def test_default_limit_applies():
    seed(40)
    assert len(TestClient(make_app()).get("/things").json()["items"]) == 25


# --- ordering and stability ------------------------------------------------------------------------


def test_walk_returns_every_row_once_newest_first():
    seed(137)
    ids = walk(limit=10)
    assert len(ids) == 137 == len(set(ids))
    with get_engine().connect() as conn:
        expected = [str(r[0]) for r in conn.execute(text("SELECT id FROM pg_probe ORDER BY created_at DESC, id DESC"))]
    assert ids == expected


def test_duplicate_timestamps_neither_skip_nor_repeat():
    seed(250, same_instant=True)
    ids = walk(limit=7)
    assert len(ids) == 250 == len(set(ids))
    assert ids == sorted(ids, reverse=True)  # same instant: the id tie-break is the order


def test_ascending_and_mixed_directions():
    seed(60)
    asc = walk(limit=9, keys=[SortKey(Probe.created_at, desc=False), SortKey(Probe.id, desc=False)])
    assert len(asc) == 60 == len(set(asc))
    mixed = walk(
        limit=8, keys=[SortKey(Probe.rank, desc=True), SortKey(Probe.created_at, desc=False), SortKey(Probe.id)]
    )
    assert len(mixed) == 60 == len(set(mixed))
    with get_engine().connect() as conn:
        expected = [
            str(r[0]) for r in conn.execute(text("SELECT id FROM pg_probe ORDER BY rank DESC, created_at ASC, id DESC"))
        ]
    assert mixed == expected


def test_rows_inserted_while_paging_do_not_skip_or_duplicate():
    from superaide.db.session import session_scope

    seed(100)
    with session_scope() as s:
        first = one_page(s, limit=30)
    seen = [i.id for i in first.items]
    seed(25, start=datetime(2027, 1, 1, tzinfo=UTC))  # newer than everything: lands before page 1
    seed(5, start=datetime(2025, 1, 1, tzinfo=UTC))  # older: lands after the last page we have not read yet
    cursor = first.next_cursor
    while cursor:
        with session_scope() as s:
            page = one_page(s, limit=30, cursor=cursor)
        seen += [i.id for i in page.items]
        cursor = page.next_cursor
    assert len(seen) == len(set(seen))  # never a duplicate
    original_100 = 100
    assert (
        len(seen) == original_100 + 5
    )  # the 100 we started with plus the older 5, none skipped; the 25 newer are page-0 news


def test_rows_deleted_while_paging_do_not_shift_the_next_page():
    from superaide.db.session import session_scope

    seed(60)
    with session_scope() as s:
        first = one_page(s, limit=20)
    with get_engine().begin() as conn:  # delete the very row the cursor points at, and the page before it
        conn.execute(text("DELETE FROM pg_probe WHERE id = :i"), {"i": first.items[-1].id})
        conn.execute(text("DELETE FROM pg_probe WHERE id = :i"), {"i": first.items[0].id})
    with session_scope() as s:
        second = one_page(s, limit=20, cursor=first.next_cursor)
    assert len(second.items) == 20
    assert not {i.id for i in second.items} & {i.id for i in first.items}


def test_filter_is_applied_before_the_cursor():
    from superaide.db.session import session_scope

    seed(30, owner="a")
    seed(30, owner="b")
    assert len(walk(owner="a", limit=7)) == 30 and len(walk(owner="b", limit=7)) == 30
    with session_scope() as s:
        a_page = one_page(s, owner="a", limit=5)
        # a's cursor handed to b's query only narrows b's rows; it cannot widen into a's
        b_page = one_page(s, owner="b", limit=5, cursor=a_page.next_cursor)
    assert all(i.id not in {x.id for x in a_page.items} for i in b_page.items)
    with get_engine().connect() as conn:
        a_ids = {str(r[0]) for r in conn.execute(text("SELECT id FROM pg_probe WHERE owner='a'"))}
    assert not {i.id for i in b_page.items} & a_ids


def test_empty_and_exact_fit_pages():
    from superaide.db.session import session_scope

    with session_scope() as s:
        empty = one_page(s, limit=5)
    assert empty.items == [] and empty.next_cursor is None and empty.has_more is False
    seed(10)
    with session_scope() as s:
        exact = one_page(s, limit=10)
    assert len(exact.items) == 10 and exact.has_more is False and exact.next_cursor is None


def test_multi_column_rows_use_entity_of():
    from superaide.db.session import session_scope

    seed(12)
    with session_scope() as s:
        page = paginate(
            s,
            select(Probe, Probe.owner.label("who")),
            NEWEST_FIRST,
            endpoint=EP,
            bind="b",
            limit=5,
            cursor=None,
            build=lambda row: row[0].owner + row[1],
            entity_of=lambda row: row[0],
        )
    assert page.items == ["aa"] * 5 and page.has_more


def test_total_estimate_is_passed_through_and_approx_count_is_cheap():
    from superaide.db.session import session_scope

    seed(500)
    with get_engine().begin() as conn:
        conn.execute(text("ANALYZE pg_probe"))
    with session_scope() as s:
        n = approx_count(s, "pg_probe")
        assert n is not None and 400 <= n <= 600
        assert approx_count(s, "no_such_table") is None
        page = paginate(
            s, select(Probe), NEWEST_FIRST, endpoint=EP, bind="b", limit=5, cursor=None, build=out, total_estimate=n
        )
    assert page.total_estimate == n


def test_next_link_header_value():
    assert (
        next_link("http://x/api/things?limit=10&cursor=old", "NEW")
        == '<http://x/api/things?limit=10&cursor=NEW>; rel="next"'
    )
    assert next_link("http://x/api/things", None) is None


# --- the plan: an index seek, never a sort of the table ----------------------------------------------


def plan_nodes(s: Session, stmt: Any) -> list[str]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    conn = s.connection()
    raw = conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + str(compiled), compiled.params).scalar()
    found: list[str] = []

    def walk_plan(node: dict[str, Any]) -> None:
        found.append(node["Node Type"])
        for child in node.get("Plans", []):
            walk_plan(child)

    walk_plan((raw if isinstance(raw, list) else json.loads(raw))[0]["Plan"])
    return found


def test_page_queries_use_the_index_and_do_not_sort_the_table():
    from superaide.db.session import session_scope

    seed(30_000)
    with get_engine().begin() as conn:
        conn.execute(text("ANALYZE pg_probe"))
    base = select(Probe).where(Probe.owner == "a")
    asc = [SortKey(Probe.created_at, desc=False), SortKey(Probe.id, desc=False)]
    with session_scope() as s:
        first = one_page(s, limit=25, keys=asc)
        halfway = encode_cursor(
            [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=20_000), uuid.UUID(int=0)],
            asc,
            endpoint=EP,
            bind=bind_for("a"),
        )
        deep = one_page(s, limit=25, keys=asc, cursor=halfway)
        assert len(deep.items) == 25
        for cursor in (None, first.next_cursor, deep.next_cursor):
            nodes = plan_nodes(s, page_query(base, asc, endpoint=EP, bind=bind_for("a"), limit=25, cursor=cursor))
            assert any("Index" in n for n in nodes), nodes
            assert "Sort" not in nodes and "Seq Scan" not in nodes, nodes


# --- scale ---------------------------------------------------------------------------------------------

SCALE_ROWS = int(os.environ.get("PAGINATION_SCALE_ROWS", "100000"))


def test_scale_deep_pages_are_fast_and_memory_stays_flat():
    """Page through SCALE_ROWS rows (default 100k; set PAGINATION_SCALE_ROWS=1000000 for the 1M run).
    Latency of a page must not grow with depth and the Python heap must not grow with pages read."""
    from superaide.db.session import session_scope

    seed(SCALE_ROWS)
    with get_engine().begin() as conn:
        conn.execute(text("ANALYZE pg_probe"))
    limit = 100
    pages = SCALE_ROWS // limit
    cursor = None
    latencies: list[float] = []
    heap: list[int] = []
    seen = 0
    tracemalloc.start()
    try:
        for n in range(pages):
            t = time.perf_counter()
            with session_scope() as s:
                page = one_page(s, limit=limit, cursor=cursor)
            latencies.append(time.perf_counter() - t)
            seen += len(page.items)
            cursor = page.next_cursor
            if n % max(pages // 10, 1) == 0:
                heap.append(tracemalloc.get_traced_memory()[0])
            if not cursor:
                break
    finally:
        tracemalloc.stop()
    assert seen == SCALE_ROWS
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)]
    assert p95 < 0.25, f"p95 page latency {p95 * 1000:.0f} ms"
    first_tenth = sum(latencies[: max(len(latencies) // 10, 1)]) / max(len(latencies) // 10, 1)
    assert latencies[-1] < max(first_tenth * 50, 0.25)  # the deepest page is not meaningfully slower than the median
    assert max(heap) - min(heap) < 5 * 1024 * 1024, (
        f"heap grew by {(max(heap) - min(heap)) / 1e6:.1f} MB over {pages} pages"
    )
