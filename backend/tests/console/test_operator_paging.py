"""Operator console paging (ADR 0014): /api/events by cursor, /api/conversations/page by last activity, and the
single maintained `context.last_event_at` every event write path must keep right."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from superaide.app.accounts import owner as hired_owner
from superaide.integrations.gmail import inbox as gmail_inbox
from superaide.main import create_app
from superaide.memory.models import Context, Event
from superaide.memory.store import log_event, touch_context
from superaide.platform.db.session import session_scope
from superaide.platform.pagination import SortKey, bind_for, encode_cursor, page_query
from tests.integrations.gmail_fake import mail, owner_client_fixture  # noqa: F401  (fixture)

ADMIN = {"Authorization": "Bearer test-admin-token", "X-Operator-Reason": "checking a reported problem"}
SRC = Path(__file__).resolve().parents[2] / "src" / "superaide"


def last_at(context_id: uuid.UUID) -> datetime:
    with session_scope() as s:
        return s.scalar(select(Context.last_event_at).where(Context.id == context_id))


def make_context(s, external: str) -> Context:
    ctx = Context(provider="web", tenant_scope="", external_id=external, kind="dm", audience_class="private_1to1")
    s.add(ctx)
    s.flush()
    return ctx


def add_event(s, ctx: Context, when: datetime) -> Event:
    from superaide.memory.audience import Audience

    e = log_event(s, context=ctx, direction="in", content="hi", audience=Audience(person_ids=set()))
    e.created_at = when
    s.flush()
    touch_context(s, e)
    return e


# --- every event write path keeps last_event_at ---------------------------------------------------------------


def test_only_two_places_construct_an_event_and_both_touch_the_context():
    """A guard: a new `Event(` anywhere else would bypass touch_context, so this fails until it is routed through
    log_event (or calls touch_context itself)."""
    pattern = re.compile(r"(?<![A-Za-z_.])(?<!class )Event\(")
    homes = {str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if pattern.search(p.read_text())}
    assert homes == {"memory/store.py", "integrations/gmail/inbox.py"}, homes
    for home in homes:
        assert "touch_context(" in (SRC / home).read_text()


def test_log_event_moves_the_conversation_forward(agent):
    with session_scope() as s:
        ctx = make_context(s, "web:a")
        cid, created = ctx.id, ctx.last_event_at
    with session_scope() as s:
        ctx = s.get(Context, cid)
        e = add_event(s, ctx, datetime.now(UTC) + timedelta(hours=1))
        stamp = e.created_at
    assert last_at(cid) == stamp > created


def test_an_event_arriving_out_of_order_never_moves_it_back(agent):
    with session_scope() as s:
        ctx = make_context(s, "web:b")
        cid = ctx.id
        newer = datetime.now(UTC) + timedelta(hours=5)
        add_event(s, ctx, newer)
        add_event(s, ctx, newer - timedelta(days=3))  # a backfilled, older message
    assert last_at(cid) == newer


def test_the_pipeline_a_chat_turn_and_the_gmail_intake_all_keep_it(owner_client, monkeypatch):
    # the owner chat (pipeline.handle -> log_event, twice per turn)
    before = datetime.now(UTC)
    assert owner_client.post("/app/chat", json={"text": "hello Ava"}).status_code == 200
    with session_scope() as s:
        web = s.scalar(select(Context).where(Context.provider == "web"))
        newest = s.scalar(select(Event.created_at).where(Event.context_id == web.id).order_by(Event.created_at.desc()))
        assert web.last_event_at == newest and web.last_event_at >= before
    # the Gmail intake (a second construction site)
    with session_scope() as s:
        owner = hired_owner(s)
        ev = gmail_inbox.ingest(s, owner, mail("m-1"))
        assert ev is not None
        ctx = s.get(Context, ev.context_id)
        assert ctx.last_event_at == ev.created_at


def test_get_or_create_context_starts_with_a_sensible_value(agent):
    with session_scope() as s:
        ctx = make_context(s, "web:c")
        assert ctx.last_event_at is not None


# --- the paged routes ----------------------------------------------------------------------------------------


@pytest.fixture
def api(agent):
    return TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})


def seed_conversations(n: int, events_each: int = 2) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with session_scope() as s:
        for i in range(n):
            ctx = make_context(s, f"web:seed-{i}")
            for j in range(events_each):
                add_event(s, ctx, base + timedelta(hours=i, minutes=j))


def test_conversations_page_walks_every_conversation_once_by_recent_activity(api):
    seed_conversations(23)
    seen: list[dict] = []
    cursor = None
    while True:
        r = api.get(
            "/api/conversations/page", params={"limit": 10, **({"cursor": cursor} if cursor else {})}, headers=ADMIN
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"items", "next_cursor", "has_more", "total_estimate"}
        seen += body["items"]
        cursor = body["next_cursor"]
        assert body["has_more"] == (cursor is not None)
        if not cursor:
            break
    assert len(seen) == 23 == len({c["id"] for c in seen})
    ats = [c["last_at"] for c in seen]
    assert ats == sorted(ats, reverse=True)  # newest activity first
    assert all(c["messages"] == 2 and c["messages_more"] is False for c in seen)


def test_conversation_message_count_stops_at_the_cap(api, monkeypatch):
    from superaide.api import admin

    monkeypatch.setattr(admin, "MESSAGE_COUNT_CAP", 3)
    seed_conversations(1, events_each=5)
    item = api.get("/api/conversations/page", headers=ADMIN).json()["items"][0]
    assert item["messages"] == 3 and item["messages_more"] is True


def test_conversations_page_needs_the_admin_token_and_a_reason_and_refuses_bad_input(api):
    assert api.get("/api/conversations/page").status_code == 401
    assert api.get("/api/conversations/page", headers={"Authorization": ADMIN["Authorization"]}).status_code == 403
    for bad in ({"limit": 0}, {"limit": 101}, {"cursor": "junk"}):
        r = api.get("/api/conversations/page", params=bad, headers=ADMIN)
        assert r.status_code == 422 and r.json()["status"] == "error"
    cursor = api.get("/api/conversations/page", params={"limit": 2}, headers=ADMIN).json()["next_cursor"]
    assert cursor is None  # nothing seeded
    seed_conversations(5)
    cursor = api.get("/api/conversations/page", params={"limit": 2}, headers=ADMIN).json()["next_cursor"]
    # a conversations cursor does not open the events list
    r = api.get("/api/events", params={"cursor": cursor}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"


def test_events_page_by_cursor_keeps_the_bare_array(api):
    seed_conversations(1, events_each=30)
    plain = api.get("/api/events", headers=ADMIN)
    assert plain.status_code == 200 and isinstance(plain.json(), list) and len(plain.json()) == 30
    assert plain.headers["X-Has-More"] == "false"
    page = api.get("/api/events", params={"limit": 10}, headers=ADMIN)
    assert (
        page.headers["X-Has-More"] == "true" and page.headers["X-Next-Cursor"] and 'rel="next"' in page.headers["Link"]
    )
    seen = page.json()
    while page.headers.get("X-Next-Cursor"):
        page = api.get("/api/events", params={"limit": 10, "cursor": page.headers["X-Next-Cursor"]}, headers=ADMIN)
        seen += page.json()
    assert len(seen) == 30 == len({e["id"] for e in seen})
    assert len(api.get("/api/events", params={"limit": 5000}, headers=ADMIN).json()) == 30  # clamped, not refused


def test_events_cursor_is_bound_to_its_conversation_filter(api):
    seed_conversations(2, events_each=12)
    with session_scope() as s:
        ctx_a, ctx_b = [c.id for c in s.scalars(select(Context).order_by(Context.external_id))]
    cur = api.get("/api/events", params={"limit": 5, "context_id": str(ctx_a)}, headers=ADMIN).headers["X-Next-Cursor"]
    assert (
        api.get("/api/events", params={"limit": 5, "context_id": str(ctx_b), "cursor": cur}, headers=ADMIN).status_code
        == 422
    )
    assert (
        api.get("/api/events", params={"limit": 5, "context_id": str(ctx_a), "cursor": cur}, headers=ADMIN).status_code
        == 200
    )


# --- plans on the real tables ---------------------------------------------------------------------------------


def explain(s, stmt) -> str:
    c = stmt.compile(dialect=postgresql.dialect())
    return str(s.connection().exec_driver_sql("EXPLAIN (FORMAT JSON) " + str(c), c.params).scalar())


def test_operator_page_queries_use_their_indexes():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with session_scope() as s:
        ctxs = [make_context(s, f"web:p-{i}") for i in range(3)]
        agent_id, tenant_id = ctxs[0].agent_id, ctxs[0].tenant_id
        ids = [c.id for c in ctxs]
        s.bulk_insert_mappings(
            Event,
            [
                {
                    "id": uuid.uuid4(),
                    "context_id": ids[i % 3],
                    "direction": "in",
                    "content": "x",
                    "created_at": base + timedelta(seconds=i),
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                }
                for i in range(30000)
            ],
        )
        s.bulk_insert_mappings(
            Context,
            [
                {
                    "id": uuid.uuid4(),
                    "provider": "web",
                    "tenant_scope": "",
                    "external_id": f"web:bulk-{i}",
                    "kind": "dm",
                    "audience_class": "private_1to1",
                    "title": "",
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                    "created_at": base,
                    "last_event_at": base + timedelta(minutes=i),
                }
                for i in range(20000)
            ],
        )
    with session_scope() as s:
        s.execute(text("ANALYZE event"))
        s.execute(text("ANALYZE context"))
        ev_keys = [SortKey(Event.created_at), SortKey(Event.id)]
        mid = encode_cursor(
            [base + timedelta(seconds=15000), uuid.UUID(int=0)], ev_keys, endpoint="x", bind=bind_for("o")
        )
        for cursor in (None, mid):
            plan = explain(
                s,
                page_query(
                    select(Event).where(Event.agent_id == agent_id),
                    ev_keys,
                    endpoint="x",
                    bind=bind_for("o"),
                    limit=200,
                    cursor=cursor,
                ),
            )
            assert "ix_event_agent_created" in plan and "'Sort'" not in plan and "Seq Scan" not in plan, plan
        cx_keys = [SortKey(Context.last_event_at), SortKey(Context.id)]
        mid = encode_cursor(
            [base + timedelta(minutes=10000), uuid.UUID(int=0)], cx_keys, endpoint="y", bind=bind_for("o")
        )
        for cursor in (None, mid):
            plan = explain(
                s,
                page_query(
                    select(Context).where(Context.agent_id == agent_id),
                    cx_keys,
                    endpoint="y",
                    bind=bind_for("o"),
                    limit=50,
                    cursor=cursor,
                ),
            )
            assert "ix_context_activity" in plan and "'Sort'" not in plan and "Seq Scan" not in plan, plan


def event_seq_scan(plan: str) -> bool:
    """A sequential scan of the event table (the small person table in the join may be scanned freely)."""
    return bool(re.search(r"'Node Type': 'Seq Scan'[^}]*'Relation Name': 'event'", plan))


def test_operator_events_query_on_skewed_data_uses_the_right_index_per_filter():
    """Many conversations and one long one: /api/events?context_id= must stay on the conversation indexes (never the
    instance-wide scan that filters by conversation), and /api/events with no filter uses ix_event_agent_created."""
    from superaide.identity.models import Person

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with session_scope() as s:
        big = make_context(s, "web:big")
        small = [make_context(s, f"tg:{i}") for i in range(600)]
        common = {"tenant_id": big.tenant_id, "agent_id": big.agent_id, "direction": "in", "content": "x"}
        rows = [
            {"id": uuid.uuid4(), "context_id": big.id, "created_at": t0 + timedelta(seconds=i), **common}
            for i in range(3000)
        ]
        rows += [
            {"id": uuid.uuid4(), "context_id": c.id, "created_at": t0 + timedelta(seconds=j * 100 + k % 100), **common}
            for k, c in enumerate(small)
            for j in range(200)
        ]
        s.bulk_insert_mappings(Event, rows)
        big_id, agent_id = big.id, big.agent_id
    keys = [SortKey(Event.created_at), SortKey(Event.id)]
    base = select(Event, Person.display_name).join(Person, Person.id == Event.resolved_person_id, isouter=True)
    with session_scope() as s:
        s.execute(text("ANALYZE event"))
        mid = encode_cursor(
            [t0 + timedelta(seconds=1500), uuid.UUID(int=2**127)], keys, endpoint="z", bind=bind_for("o")
        )
        for cursor in (None, mid):
            plan = explain(
                s,
                page_query(
                    base.where(Event.context_id == big_id, Event.agent_id == agent_id),
                    keys,
                    endpoint="z",
                    bind=bind_for("o"),
                    limit=200,
                    cursor=cursor,
                ),
            )
            assert "ix_event_agent_created" not in plan and not event_seq_scan(plan), plan
            if cursor is None:
                assert "ix_event_context_created" in plan and "'Sort'" not in plan, plan
        for cursor in (None, mid):
            plan = explain(
                s,
                page_query(
                    base.where(Event.agent_id == agent_id),
                    keys,
                    endpoint="z",
                    bind=bind_for("o"),
                    limit=200,
                    cursor=cursor,
                ),
            )
            assert "ix_event_agent_created" in plan and "'Sort'" not in plan and not event_seq_scan(plan), plan


# --- memories, approvals, people (ADR 0014, 0079) ----------------------------------------------------------------


def walk_page(api, path: str, limit: int = 10) -> list[dict]:
    seen: list[dict] = []
    cursor = None
    while True:
        r = api.get(path, params={"limit": limit, **({"cursor": cursor} if cursor else {})}, headers=ADMIN)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"items", "next_cursor", "has_more", "total_estimate"}
        seen += body["items"]
        cursor = body["next_cursor"]
        assert body["has_more"] == (cursor is not None)
        if not cursor:
            return seen


def seed_people(n: int, identities_each: int = 1, unlinked: int = 0) -> None:
    from superaide.identity.models import ChannelIdentity, IdentityLinkEvent, Person

    base = datetime(2026, 1, 1, tzinfo=UTC)
    with session_scope() as s:
        for i in range(n):
            p = Person(display_name=f"Person {i}", relation="external", created_at=base + timedelta(minutes=i))
            s.add(p)
            s.flush()
            for j in range(identities_each):
                ident = ChannelIdentity(person_id=p.id, provider="telegram", stable_key=f"tg-{i}-{j}")
                s.add(ident)
                s.flush()
                s.add(IdentityLinkEvent(identity_id=ident.id, person_id=p.id, action="link", method="x", actor="op"))
        for k in range(unlinked):
            s.add(
                ChannelIdentity(
                    person_id=None, provider="slack", stable_key=f"stranger-{k}", created_at=base + timedelta(minutes=k)
                )
            )


def test_memories_and_approvals_pages_walk_every_row_once_newest_first(api):
    from superaide.approvals.models import Approval
    from superaide.memory.models import MemoryItem

    base = datetime(2026, 1, 1, tzinfo=UTC)
    with session_scope() as s:
        for i in range(23):
            s.add(MemoryItem(content=f"fact {i}", created_at=base + timedelta(minutes=i)))
            s.add(Approval(kind="send_email", payload={"summary": f"s{i}"}, created_at=base + timedelta(minutes=i)))
    for path in ("/api/memories/page", "/api/approvals/page"):
        rows = walk_page(api, path)
        assert len(rows) == 23 == len({r["id"] for r in rows})
        ats = [r["at"] for r in rows]
        assert ats == sorted(ats, reverse=True)
    mem = walk_page(api, "/api/memories/page")
    assert set(mem[0]) == {"id", "content", "allowed_kind", "allowed_people", "min_trust", "at"}
    # same rows as the unpaged route (additive: it still answers)
    assert len(api.get("/api/memories", headers=ADMIN).json()) == 23


def test_people_page_nests_identities_and_bounds_link_events(api, monkeypatch):
    from superaide.api import admin

    monkeypatch.setattr(admin, "LINK_EVENTS_PER_PERSON", 2)
    seed_people(12, identities_each=3, unlinked=7)
    rows = walk_page(api, "/api/people/page", limit=5)
    assert len(rows) == 12 == len({r["id"] for r in rows})
    assert all(len(r["identities"]) == 3 for r in rows)
    assert all(len(r["link_events"]) == 2 for r in rows)  # 3 happened, the newest 2 are shown
    assert rows[0]["name"] == "Person 0"  # oldest first, like the old route
    stray = walk_page(api, "/api/people/unlinked/page", limit=3)
    assert len(stray) == 7 == len({r["id"] for r in stray})
    assert all(r["stable_key"].startswith("stranger-") for r in stray)


@pytest.mark.parametrize(
    "path", ["/api/memories/page", "/api/approvals/page", "/api/people/page", "/api/people/unlinked/page"]
)
def test_operator_list_pages_are_gated_and_validated(api, path):
    assert api.get(path).status_code == 401
    assert api.get(path, headers={"Authorization": ADMIN["Authorization"]}).status_code == 403  # no reason
    for bad in ({"limit": 0}, {"limit": 101}, {"cursor": "junk"}):
        r = api.get(path, params=bad, headers=ADMIN)
        assert r.status_code == 422 and r.json()["status"] == "error"


def test_a_cursor_does_not_move_between_operator_lists(api):
    seed_people(6)
    cursor = api.get("/api/people/page", params={"limit": 2}, headers=ADMIN).json()["next_cursor"]
    assert cursor
    for other in ("/api/memories/page", "/api/approvals/page", "/api/conversations/page"):
        r = api.get(other, params={"cursor": cursor}, headers=ADMIN)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"


def test_operator_list_queries_use_their_indexes():
    from superaide.approvals.models import Approval
    from superaide.identity.models import ChannelIdentity, Person
    from superaide.memory.models import MemoryItem

    base = datetime(2026, 1, 1, tzinfo=UTC)
    with session_scope() as s:
        agent_id = s.scalar(select(text("current_setting('server_version')")))  # warm the connection
        s.add(Context(provider="web", tenant_scope="", external_id="probe", kind="dm", audience_class="private_1to1"))
        s.flush()
        agent_id = s.scalar(select(Context.agent_id))
        tenant_id = s.scalar(select(Context.tenant_id))
        common = {"tenant_id": tenant_id, "agent_id": agent_id}
        s.bulk_insert_mappings(
            MemoryItem,
            [
                {"id": uuid.uuid4(), "content": "x", "created_at": base + timedelta(seconds=i), **common}
                for i in range(20000)
            ],
        )
        s.bulk_insert_mappings(
            Approval,
            [
                {"id": uuid.uuid4(), "kind": "k", "created_at": base + timedelta(seconds=i), **common}
                for i in range(20000)
            ],
        )
        s.bulk_insert_mappings(
            Person,
            [
                {"id": uuid.uuid4(), "display_name": "p", "created_at": base + timedelta(seconds=i), **common}
                for i in range(20000)
            ],
        )
        s.bulk_insert_mappings(
            ChannelIdentity,
            [
                {
                    "id": uuid.uuid4(),
                    "provider": "slack",
                    "stable_key": f"k{i}",
                    "created_at": base + timedelta(seconds=i),
                    **common,
                }
                for i in range(20000)
            ],
        )
    # Skew: three other instances hold three times as many rows as ours, over the same time range, so the
    # instance filter is what makes the index worth using (the planner must not scan everyone's rows).
    with session_scope() as s:
        for _ in range(3):
            other = {"tenant_id": tenant_id, "agent_id": uuid.uuid4()}
            s.bulk_insert_mappings(
                MemoryItem,
                [
                    {"id": uuid.uuid4(), "content": "x", "created_at": base + timedelta(seconds=i), **other}
                    for i in range(20000)
                ],
            )
            s.bulk_insert_mappings(
                Approval,
                [
                    {"id": uuid.uuid4(), "kind": "k", "created_at": base + timedelta(seconds=i), **other}
                    for i in range(20000)
                ],
            )
            s.bulk_insert_mappings(
                Person,
                [
                    {"id": uuid.uuid4(), "display_name": "p", "created_at": base + timedelta(seconds=i), **other}
                    for i in range(20000)
                ],
            )
            s.bulk_insert_mappings(
                ChannelIdentity,
                [
                    {
                        "id": uuid.uuid4(),
                        "provider": "slack",
                        "stable_key": f"o{i}",
                        "created_at": base + timedelta(seconds=i),
                        **other,
                    }
                    for i in range(20000)
                ],
            )
    cases = [
        (MemoryItem, True, "ix_memory_item_agent_created", select(MemoryItem)),
        (Approval, True, "ix_approval_agent_created", select(Approval)),
        (Person, False, "ix_person_agent_created", select(Person)),
        (
            ChannelIdentity,
            False,
            "ix_identity_unlinked",
            select(ChannelIdentity).where(ChannelIdentity.person_id.is_(None)),
        ),
    ]
    with session_scope() as s:
        for model, desc, index, base_q in cases:
            s.execute(text(f"ANALYZE {model.__tablename__}"))
            keys = [SortKey(model.created_at, desc=desc), SortKey(model.id, desc=desc)]
            mid = encode_cursor(
                [base + timedelta(seconds=10000), uuid.UUID(int=0 if not desc else 2**127)],
                keys,
                endpoint="z",
                bind=bind_for("o"),
            )
            for cursor in (None, mid):
                plan = explain(
                    s,
                    page_query(
                        base_q.where(model.agent_id == agent_id),
                        keys,
                        endpoint="z",
                        bind=bind_for("o"),
                        limit=50,
                        cursor=cursor,
                    ),
                )
                assert index in plan and "'Sort'" not in plan and "Seq Scan" not in plan, (model.__name__, plan)
