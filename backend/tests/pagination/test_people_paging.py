"""/app/people pages by the standard cursor (ADR 0014): VIPs first then by name, no query per contact, a cursor bound
to its owner, and an index scan of the owner's kept contacts on skewed data."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.dialects import postgresql

from superaide.app.accounts import owner as hired_owner
from superaide.main import create_app
from superaide.platform.db.models import ChannelIdentity, Contact, Person
from superaide.platform.db.session import get_engine, session_scope
from superaide.platform.pagination import SortKey, bind_for, encode_cursor, page_query

PASSWORD = "correct horse battery"


@pytest.fixture
def browser(agent):
    app = create_app(agent)

    def make() -> TestClient:
        return TestClient(app, headers={"X-Requested-With": "superaide"})

    return make


def hired(browser, email: str = "a@example.com") -> tuple[TestClient, uuid.UUID]:
    c = browser()
    assert c.post("/auth/signup", json={"name": "Meera Iyer", "email": email, "password": PASSWORD}).status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c, uuid.UUID(c.get("/app/me").json()["person"]["id"])


def add_contacts(owner: uuid.UUID, names: list[str], *, vip: set[str] = frozenset(), channels: bool = False) -> None:
    with session_scope() as s:
        for n in names:
            p = Person(display_name=n, relation="external")
            s.add(p)
            s.flush()
            s.add(Contact(owner_person_id=owner, person_id=p.id, relationship="team", vip=n in vip))
            if channels:
                s.add(ChannelIdentity(person_id=p.id, provider="telegram", stable_key=f"tg-{n}-{uuid.uuid4()}"))
                s.add(ChannelIdentity(person_id=p.id, provider="slack", stable_key=f"sl-{n}-{uuid.uuid4()}"))


def walk(c: TestClient, **params) -> list[dict]:
    seen: list[dict] = []
    cursor = None
    while True:
        r = c.get("/app/people", params={**params, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"people", "next_cursor", "has_more"}
        seen += body["people"]
        cursor = body["next_cursor"]
        assert body["has_more"] == (cursor is not None)
        if not cursor:
            return seen


def test_vips_first_then_by_name_across_pages_with_no_repeats(browser):
    c, owner = hired(browser)
    names = [f"Person {i:03d}" for i in range(57)]
    add_contacts(owner, names, vip={"Person 050", "Person 003", "Person 020"})
    rows = walk(c, limit=10)
    assert len(rows) == 57 == len({r["id"] for r in rows})
    assert [r["name"] for r in rows[:3]] == ["Person 003", "Person 020", "Person 050"]  # VIPs, by name
    assert all(not r["vip"] for r in rows[3:])
    assert [r["name"] for r in rows[3:]] == sorted(r["name"] for r in rows[3:])
    plain = c.get("/app/people").json()
    assert len(plain["people"]) == 57 and plain["has_more"] is False and plain["next_cursor"] is None
    assert set(plain["people"][0]) == {"id", "name", "relationship", "vip", "channels"}


def test_two_people_with_the_same_name_neither_skip_nor_repeat(browser):
    c, owner = hired(browser)
    add_contacts(owner, ["Priya Nair"] * 7 + ["Zed"])
    rows = walk(c, limit=3)
    assert len(rows) == 8 == len({r["id"] for r in rows})


def test_removed_contacts_are_not_listed_and_channels_are_per_person(browser):
    c, owner = hired(browser)
    add_contacts(owner, ["Asha", "Bela", "Chitra"], channels=True)
    with session_scope() as s:
        gone = s.scalar(
            select(Contact).join(Person, Person.id == Contact.person_id).where(Person.display_name == "Bela")
        )
        gone.removed_at = datetime.now(UTC)
    rows = walk(c, limit=2)
    assert [r["name"] for r in rows] == ["Asha", "Chitra"]
    assert all(r["channels"] == ["slack", "telegram"] for r in rows)


def test_a_page_costs_a_fixed_number_of_queries_not_one_per_contact(browser):
    c, owner = hired(browser)
    add_contacts(owner, [f"P{i:02d}" for i in range(40)], channels=True)
    counted: list[str] = []

    def count(conn, cursor, statement, *a):
        counted.append(statement)

    event.listen(get_engine(), "before_cursor_execute", count)
    try:
        assert len(c.get("/app/people", params={"limit": 40}).json()["people"]) == 40
    finally:
        event.remove(get_engine(), "before_cursor_execute", count)
    channel_queries = [q for q in counted if "channel_identity" in q and "SELECT" in q]
    assert len(channel_queries) <= 2, len(channel_queries)  # session + one for the page; it was 40


def test_cursor_is_bound_to_its_owner_and_bad_input_is_a_422(browser):
    a, a_id = hired(browser, "a@example.com")
    b, b_id = hired(browser, "b@example.com")
    add_contacts(a_id, [f"A{i}" for i in range(12)])
    add_contacts(b_id, [f"B{i}" for i in range(12)])
    cursor = a.get("/app/people", params={"limit": 3}).json()["next_cursor"]
    r = b.get("/app/people", params={"cursor": cursor})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"
    assert all(p["name"].startswith("A") for p in walk(a, limit=5))
    assert all(p["name"].startswith("B") for p in walk(b, limit=5))
    for bad in ({"limit": 0}, {"limit": 201}, {"cursor": "junk"}):
        assert a.get("/app/people", params=bad).status_code == 422


def test_people_page_uses_an_index_scan_of_the_owners_contacts_on_skewed_data(browser):
    """One owner with 3000 contacts among three other owners with 30000 each and many removed contacts."""
    c, owner = hired(browser)
    with session_scope() as s:
        me = hired_owner(s)
        agent_id, tenant_id = me.agent_id, me.tenant_id
        common = {"tenant_id": tenant_id, "agent_id": agent_id}
        mine = [{"id": uuid.uuid4(), "display_name": f"Mine {i:05d}", **common} for i in range(3000)]
        s.bulk_insert_mappings(Person, mine)
        s.bulk_insert_mappings(
            Contact,
            [
                {
                    "id": uuid.uuid4(),
                    "owner_person_id": owner,
                    "person_id": p["id"],
                    "relationship": "x",
                    "vip": i % 40 == 0,
                    "notes": "",
                    **common,
                }
                for i, p in enumerate(mine)
            ],
        )
        gone = [{"id": uuid.uuid4(), "display_name": f"Gone {i}", **common} for i in range(6000)]
        s.bulk_insert_mappings(Person, gone)
        s.bulk_insert_mappings(
            Contact,
            [
                {
                    "id": uuid.uuid4(),
                    "owner_person_id": owner,
                    "person_id": p["id"],
                    "relationship": "x",
                    "vip": False,
                    "notes": "",
                    "removed_at": datetime.now(UTC),
                    **common,
                }
                for p in gone
            ],
        )
        for _ in range(3):
            other_owner = uuid.uuid4()
            s.bulk_insert_mappings(Person, [{"id": other_owner, "display_name": "Other owner", **common}])
            theirs = [{"id": uuid.uuid4(), "display_name": f"Other {i}", **common} for i in range(10000)]
            s.bulk_insert_mappings(Person, theirs)
            s.bulk_insert_mappings(
                Contact,
                [
                    {
                        "id": uuid.uuid4(),
                        "owner_person_id": other_owner,
                        "person_id": p["id"],
                        "relationship": "x",
                        "vip": False,
                        "notes": "",
                        **common,
                    }
                    for p in theirs
                ],
            )
    keys = [SortKey(Contact.vip), SortKey(Person.display_name, desc=False), SortKey(Contact.id, desc=False)]
    mid = encode_cursor([False, "Mine 01500", uuid.UUID(int=0)], keys, endpoint="p", bind=bind_for("o"))
    with session_scope() as s:
        s.execute(text("ANALYZE contact"))
        s.execute(text("ANALYZE person"))
        for cursor in (None, mid):
            stmt = (
                select(Contact, Person)
                .join(Person, Person.id == Contact.person_id)
                .where(Contact.owner_person_id == owner, Contact.removed_at.is_(None), Contact.agent_id == agent_id)
            )
            q = page_query(stmt, keys, endpoint="p", bind=bind_for("o"), limit=50, cursor=cursor)
            cc = q.compile(dialect=postgresql.dialect())
            plan = str(s.connection().exec_driver_sql("EXPLAIN (FORMAT JSON) " + str(cc), cc.params).scalar())
            assert (
                "ix_contact_owner_active" in plan
                or "ix_contact_owner_person_id" in plan
                or "uq_contact_owner_person" in plan
            ), plan
            import re

            assert not re.search(r"'Node Type': 'Seq Scan'[^}]*'Relation Name': 'contact'", plan), plan
