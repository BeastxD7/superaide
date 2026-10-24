"""/app/notebook pages by the standard cursor (ADR 0014): the plain URL still answers, older notes are reachable,
a cursor is bound to its owner, and the page query uses the live-notes index on skewed data."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from superaide.app.accounts import owner as hired_owner
from superaide.db.models import MemoryItem
from superaide.db.session import session_scope
from superaide.main import create_app
from superaide.pagination import SortKey, bind_for, encode_cursor, page_query

PASSWORD = "correct horse battery"
BASE = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def browser(agent):
    app = create_app(agent)

    def make() -> TestClient:
        return TestClient(app, headers={"X-Requested-With": "superaide"})

    return make


def hired(browser, email: str) -> tuple[TestClient, uuid.UUID]:
    c = browser()
    assert c.post("/auth/signup", json={"name": "Meera Iyer", "email": email, "password": PASSWORD}).status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c, c.get("/app/me").json()["person"]["id"]


def seed(person: str, n: int, *, category: str = "about", prefix: str = "note") -> None:
    pid = uuid.UUID(person)
    with session_scope() as s:
        for i in range(n):
            s.add(
                MemoryItem(
                    content=f"{prefix} {i}",
                    category=category,
                    subject_person_ids=[pid],
                    created_at=BASE + timedelta(minutes=i),
                )
            )


def walk(c: TestClient, **params) -> list[dict]:
    seen: list[dict] = []
    cursor = None
    while True:
        r = c.get("/app/notebook", params={**params, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"memories", "next_cursor", "has_more"}
        seen += body["memories"]
        cursor = body["next_cursor"]
        assert body["has_more"] == (cursor is not None)
        if not cursor:
            return seen


def test_plain_url_keeps_its_shape_and_says_when_older_notes_exist(browser):
    c, person = hired(browser, "a@example.com")
    seed(person, 230)
    body = c.get("/app/notebook").json()
    assert len(body["memories"]) == 200 and body["has_more"] is True and body["next_cursor"]
    ats = [m["at"] for m in body["memories"]]
    assert ats == sorted(ats, reverse=True)  # newest first, as before
    assert set(body["memories"][0]) == {
        "id",
        "text",
        "category",
        "source",
        "document",
        "passage",
        "shareable",
        "private",
        "hearsay",
        "at",
    }


def test_the_cursor_reaches_every_note_exactly_once(browser):
    c, person = hired(browser, "a@example.com")
    seed(person, 123)
    everything = walk(c, limit=25)
    ids = [m["id"] for m in everything]
    assert len(ids) == len(set(ids))
    assert sum(1 for m in everything if m["text"].startswith("note ")) == 123
    assert [m["at"] for m in everything] == sorted((m["at"] for m in everything), reverse=True)


def test_search_and_category_filters_page_too(browser):
    c, person = hired(browser, "a@example.com")
    seed(person, 30, category="about", prefix="plain")
    seed(person, 12, category="work", prefix="gym schedule")
    hits = walk(c, limit=5, q="GYM")
    assert len(hits) == 12 and all("gym" in m["text"] for m in hits)
    assert len(walk(c, limit=5, category="work")) == 12
    assert walk(c, q="100%_match") == []  # wildcard characters are literal
    assert len(walk(c, limit=7, q="plain", category="about")) == 30


def test_forgotten_and_disputed_notes_are_not_listed(browser):
    c, person = hired(browser, "a@example.com")
    seed(person, 5)
    with session_scope() as s:
        for m in s.scalars(select(MemoryItem).where(MemoryItem.content.in_(["note 0", "note 1"]))):
            if m.content == "note 0":
                m.valid_to = datetime.now(UTC)
            else:
                m.conflict_status = "pending"
    assert {m["text"] for m in walk(c, limit=2)} == {"note 2", "note 3", "note 4"}


def test_cursor_is_bound_to_its_owner_and_bad_input_is_a_422(browser):
    a, a_person = hired(browser, "a@example.com")
    b, b_person = hired(browser, "b@example.com")
    seed(a_person, 10, prefix="mine")
    seed(b_person, 10, prefix="theirs")
    cursor = a.get("/app/notebook", params={"limit": 3}).json()["next_cursor"]
    r = b.get("/app/notebook", params={"cursor": cursor})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"
    assert all(m["text"].startswith("mine") for m in walk(a, limit=4))
    assert all(m["text"].startswith("theirs") for m in walk(b, limit=4))
    for bad in ({"limit": 0}, {"limit": 201}, {"cursor": "junk"}, {"q": "x" * 201}):
        assert a.get("/app/notebook", params=bad).status_code == 422
    assert a.get("/app/notebook", params={"cursor": cursor[:-3] + "AAA"}).status_code == 422


def test_notebook_page_uses_the_live_notes_index_on_skewed_data(browser):
    """The owner's live notes sit among many forgotten ones and many facts about other people in the same instance."""
    c, person = hired(browser, "a@example.com")
    pid = uuid.UUID(person)
    with session_scope() as s:
        owner = hired_owner(s)
        agent_id, tenant_id = owner.agent_id, owner.tenant_id
        common = {"tenant_id": tenant_id, "agent_id": agent_id, "content": "x", "category": "about"}
        s.bulk_insert_mappings(
            MemoryItem,
            [
                {"id": uuid.uuid4(), "subject_person_ids": [pid], "created_at": BASE + timedelta(seconds=i), **common}
                for i in range(3000)
            ],
        )
        s.bulk_insert_mappings(  # forgotten history of the owner
            MemoryItem,
            [
                {
                    "id": uuid.uuid4(),
                    "subject_person_ids": [pid],
                    "valid_to": BASE + timedelta(days=400),
                    "created_at": BASE + timedelta(seconds=i),
                    **common,
                }
                for i in range(40000)
            ],
        )
        other = uuid.uuid4()
        s.bulk_insert_mappings(  # facts about other people
            MemoryItem,
            [
                {"id": uuid.uuid4(), "subject_person_ids": [other], "created_at": BASE + timedelta(seconds=i), **common}
                for i in range(40000)
            ],
        )
    keys = [SortKey(MemoryItem.created_at), SortKey(MemoryItem.id)]
    mid = encode_cursor([BASE + timedelta(seconds=1500), uuid.UUID(int=2**127)], keys, endpoint="n", bind=bind_for("o"))
    with session_scope() as s:
        s.execute(text("ANALYZE memory_item"))
        for cursor in (None, mid):
            q = page_query(
                select(MemoryItem).where(
                    MemoryItem.agent_id == agent_id,
                    MemoryItem.subject_person_ids.contains([pid]),
                    MemoryItem.valid_to.is_(None),
                    MemoryItem.conflict_status == "none",
                ),
                keys,
                endpoint="n",
                bind=bind_for("o"),
                limit=50,
                cursor=cursor,
            )
            cc = q.compile(dialect=postgresql.dialect())
            plan = str(s.connection().exec_driver_sql("EXPLAIN (FORMAT JSON) " + str(cc), cc.params).scalar())
            assert "ix_memory_item_live_created" in plan, plan
            assert "'Sort'" not in plan and "Seq Scan" not in plan, plan
