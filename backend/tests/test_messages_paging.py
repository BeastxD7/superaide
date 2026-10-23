"""Chat history is paged: the newest messages first, and `before` reaches the older ones (#299)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from superaide.main import create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def browser(agent):
    app = create_app(agent)

    def make() -> TestClient:
        return TestClient(app, headers={"X-Requested-With": "superaide"})

    return make


def hired(browser, email: str) -> TestClient:
    c = browser()
    assert (
        c.post("/auth/signup", json={"name": "Shashank Rao", "email": email, "password": PASSWORD}).status_code == 200
    )
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def test_older_messages_can_be_reached_page_by_page(browser, model):
    owner = hired(browser, "s@example.com")
    for i in range(1, 6):
        assert owner.post("/app/chat", json={"text": f"message number {i}"}).status_code == 200
    everything = owner.get("/app/messages").json()
    assert len(everything) >= 11  # the greeting, five of yours and five replies

    newest = owner.get("/app/messages", params={"limit": 4}).json()
    assert [m["id"] for m in newest] == [m["id"] for m in everything[-4:]]  # oldest first within the page

    seen = newest
    while True:  # walk back to the very first message
        older = owner.get("/app/messages", params={"limit": 4, "before": seen[0]["id"]}).json()
        if not older:
            break
        assert len(older) <= 4
        assert not {m["id"] for m in older} & {m["id"] for m in seen}  # nothing twice
        seen = older + seen
    assert [m["id"] for m in seen] == [m["id"] for m in everything]
    assert seen[0]["from"] == "agent"  # the introduction greeting is reachable again


def test_before_never_reaches_into_someone_elses_conversation(browser, model):
    owner = hired(browser, "s@example.com")
    other = hired(browser, "t@example.com")
    owner.post("/app/chat", json={"text": "private to me"})
    theirs = other.get("/app/messages").json()
    mine = owner.get("/app/messages").json()
    assert owner.get("/app/messages", params={"before": theirs[-1]["id"]}).json() == []
    assert owner.get("/app/messages", params={"before": "00000000-0000-4000-8000-0000000000ff"}).json() == []
    assert owner.get("/app/messages", params={"before": mine[-1]["id"]}).json() != []
    assert owner.get("/app/messages", params={"before": "not-a-uuid"}).status_code == 422


# --- the standard cursor (ADR 0014) --------------------------------------------------------------------------


def test_plain_url_still_returns_the_newest_page_oldest_first(browser, model):
    owner = hired(browser, "s@example.com")
    owner.post("/app/chat", json={"text": "hello there"})
    r = owner.get("/app/messages")
    assert r.status_code == 200 and isinstance(r.json(), list)
    ats = [m["at"] for m in r.json()]
    assert ats == sorted(ats)
    assert r.headers["X-Has-More"] == "false" and "X-Next-Cursor" not in r.headers


def test_cursor_walks_back_to_the_start_exactly_once(browser, model):
    owner = hired(browser, "s@example.com")
    for i in range(1, 8):
        owner.post("/app/chat", json={"text": f"message number {i}"})
    everything = owner.get("/app/messages").json()
    page = owner.get("/app/messages", params={"limit": 5})
    assert page.headers["X-Has-More"] == "true" and page.headers["X-Next-Cursor"]
    assert 'rel="next"' in page.headers["Link"]
    seen = page.json()
    while page.headers.get("X-Next-Cursor"):
        page = owner.get("/app/messages", params={"limit": 5, "cursor": page.headers["X-Next-Cursor"]})
        assert page.status_code == 200 and len(page.json()) <= 5
        assert not {m["id"] for m in page.json()} & {m["id"] for m in seen}
        seen = page.json() + seen
    assert [m["id"] for m in seen] == [m["id"] for m in everything]
    assert page.headers["X-Has-More"] == "false"


def test_before_and_cursor_give_the_same_older_page(browser, model):
    owner = hired(browser, "s@example.com")
    for i in range(1, 6):
        owner.post("/app/chat", json={"text": f"m{i}"})
    first = owner.get("/app/messages", params={"limit": 4})
    by_cursor = owner.get("/app/messages", params={"limit": 4, "cursor": first.headers["X-Next-Cursor"]}).json()
    by_before = owner.get("/app/messages", params={"limit": 4, "before": first.json()[0]["id"]}).json()
    assert by_cursor == by_before and by_cursor


@pytest.mark.parametrize("limit", [0, 201, -3])
def test_limit_outside_bounds_is_a_422_envelope(browser, model, limit):
    owner = hired(browser, "s@example.com")
    r = owner.get("/app/messages", params={"limit": limit})
    assert r.status_code == 422 and r.json()["status"] == "error"


def test_a_cursor_is_refused_for_another_owner_and_when_edited(browser, model):
    owner = hired(browser, "s@example.com")
    other = hired(browser, "t@example.com")
    for i in range(4):
        owner.post("/app/chat", json={"text": f"mine {i}"})
        other.post("/app/chat", json={"text": f"theirs {i}"})
    cursor = owner.get("/app/messages", params={"limit": 3}).headers["X-Next-Cursor"]
    r = other.get("/app/messages", params={"cursor": cursor})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"
    r = owner.get("/app/messages", params={"cursor": cursor[:-3] + "AAA"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"
    assert owner.get("/app/messages", params={"cursor": "garbage"}).status_code == 422


def test_messages_page_query_uses_the_conversation_index():
    """EXPLAIN on the real table: the page is an index scan on ix_event_context_created, never a sort of the table."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, text
    from sqlalchemy.dialects import postgresql

    from superaide.db.models import Context, Event
    from superaide.db.session import session_scope
    from superaide.pagination import SortKey, bind_for, page_query

    with session_scope() as s:
        ctx = Context(provider="web", tenant_scope="", external_id="web:x", kind="dm", audience_class="private_1to1")
        s.add(ctx)
        s.flush()
        ctx_id = ctx.id
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        s.bulk_insert_mappings(
            Event,
            [
                {
                    "id": uuid.uuid4(),
                    "context_id": ctx_id,
                    "direction": "in",
                    "content": "x",
                    "created_at": t0 + timedelta(seconds=i),
                    "tenant_id": ctx.tenant_id,
                    "agent_id": ctx.agent_id,
                }
                for i in range(20000)
            ],
        )
    with session_scope() as s:
        s.execute(text("ANALYZE event"))
        keys = [SortKey(Event.created_at), SortKey(Event.id)]
        from superaide.pagination import encode_cursor

        mid = encode_cursor(
            [t0 + timedelta(seconds=10000), uuid.UUID(int=0)], keys, endpoint="messages", bind=bind_for("p")
        )
        for cursor in (None, mid):
            q = page_query(
                select(Event).where(Event.context_id == ctx_id, Event.agent_id == ctx.agent_id),
                keys,
                endpoint="messages",
                bind=bind_for("p"),
                limit=200,
                cursor=cursor,
            )
            c = q.compile(dialect=postgresql.dialect())
            plan = s.connection().exec_driver_sql("EXPLAIN (FORMAT JSON) " + str(c), c.params).scalar()
            text_plan = str(plan)
            assert "ix_event_context_created" in text_plan, text_plan
            assert "'Sort'" not in text_plan and "Seq Scan" not in text_plan, text_plan
