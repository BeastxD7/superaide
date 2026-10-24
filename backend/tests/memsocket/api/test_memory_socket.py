"""Memory Socket (#138): a read-only MCP endpoint for the owner's other assistants. Off by default; a token per
consumer; the audience gate filters in SQL before anything is returned; no write tools; a visible access log."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.app.deps import _hired_instance
from superaide.config import get_settings
from superaide.db.models import MemoryItem, UserAccount
from superaide.db.session import session_scope
from superaide.memsocket import service
from superaide.memsocket.models import MemorySocketClient
from superaide.tenancy.context import all_instances, use_instance
from tests.api_support import OTHER, signup

PUBLIC_FACT = "Ava answers scheduling questions on weekdays"
ORG_FACT = "The team offsite is in Lisbon in March"
PRIVATE_FACT = "Jordan's passport number is on the blue folder"
SECRET_FACT = "The acquisition codename is Bluebird acquisition"


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(get_settings(), "memory_socket_enabled", True)
    service._calls.clear()


@pytest.fixture
def facts(owner):
    with all_instances(), session_scope() as s:
        uid = s.scalar(select(UserAccount.id).order_by(UserAccount.created_at))
        scope, person_id = _hired_instance(uid)
    with use_instance(scope), session_scope() as s:
        s.add_all(
            [
                MemoryItem(content=PUBLIC_FACT, allowed_kind="public", min_trust=0),
                MemoryItem(content=ORG_FACT, allowed_kind="org", min_trust=2),
                MemoryItem(content=PRIVATE_FACT, allowed_kind="persons", allowed_person_ids=[person_id], min_trust=2),
                MemoryItem(content=SECRET_FACT, allowed_kind="persons", allowed_person_ids=[person_id], min_trust=4),
            ]
        )
    return scope


def add(owner, name="Claude", scope="public") -> str:
    r = owner.post("/app/memory-socket/clients", json={"name": name, "scope": scope})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def rpc(browser, token: str | None, method: str, params=None, rid=1):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"jsonrpc": "2.0", "method": method, **({"id": rid} if rid is not None else {})}
    if params is not None:
        body["params"] = params
    return browser().post("/mcp/memory", json=body, headers=headers)


def ask(browser, token, question):
    r = rpc(browser, token, "tools/call", {"name": "ask_ava_memory", "arguments": {"question": question}})
    assert r.status_code == 200, r.text
    return r.json()["result"]["content"][0]["text"]


def test_off_by_default_everything_is_404(browser, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "memory_socket_enabled", False)
    assert rpc(browser, "sa_mem_x", "tools/list").status_code == 404
    assert owner.get("/app/memory-socket").status_code == 404


def test_no_token_wrong_token_and_a_revoked_token_are_401(browser, owner):
    token = add(owner)
    assert rpc(browser, None, "tools/list").status_code == 401
    assert rpc(browser, "sa_mem_nope", "tools/list").status_code == 401
    assert rpc(browser, token, "tools/list").status_code == 200
    client_id = owner.get("/app/memory-socket").json()["clients"][0]["id"]
    assert owner.post(f"/app/memory-socket/clients/{client_id}/revoke").status_code == 200
    assert rpc(browser, token, "tools/list").status_code == 401


def test_the_token_is_shown_once_and_only_its_hash_is_stored(owner):
    token = add(owner)
    assert token not in owner.get("/app/memory-socket").text
    with all_instances(), session_scope() as s:
        row = s.scalar(select(MemorySocketClient))
        assert row.token_hash == service.hash_token(token) and token not in row.token_hash


def test_one_read_only_tool_and_no_write_tools(browser, owner):
    token = add(owner)
    tools = rpc(browser, token, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["ask_ava_memory"] and tools[0]["annotations"]["readOnlyHint"] is True
    for name in ("remember", "delete_memory", "write_memory"):
        r = rpc(browser, token, "tools/call", {"name": name, "arguments": {"question": "x"}}).json()
        assert r["error"]["code"] == -32602


def test_initialize_and_notifications(browser, owner):
    token = add(owner)
    r = rpc(browser, token, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert r["protocolVersion"] == "2025-06-18" and r["capabilities"] == {"tools": {}}
    assert rpc(browser, token, "notifications/initialized", rid=None).status_code == 202
    assert rpc(browser, token, "nope").json()["error"]["code"] == -32601


def test_public_scope_returns_only_public_memories(browser, owner, facts):
    token = add(owner, scope="public")
    text = ask(browser, token, "scheduling questions weekdays team offsite Lisbon passport number blue folder")
    assert "weekdays" in text
    assert "Lisbon" not in text and "passport" not in text and "Bluebird" not in text


def test_owner_scope_returns_owner_memories_but_never_ones_needing_a_step_up(browser, owner, facts):
    token = add(owner, scope="owner")
    text = ask(browser, token, "team offsite Lisbon passport number blue folder acquisition codename Bluebird")
    assert "Lisbon" in text and "passport" in text
    assert "Bluebird" not in text  # min_trust 4: stays in Ava


def test_each_consumer_has_its_own_scope(browser, owner, facts):
    pub, own = add(owner, "A", "public"), add(owner, "B", "owner")
    assert "Lisbon" not in ask(browser, pub, "team offsite Lisbon")
    assert "Lisbon" in ask(browser, own, "team offsite Lisbon")


def test_nothing_from_another_owner_is_ever_returned(browser, owner, other, facts):
    from tests.api_support import hire

    assert hire(other).status_code == 200  # a second owner with their own, empty Ava
    other_token = add(other, "Gemini", "owner")
    text = ask(browser, other_token, "team offsite Lisbon passport Bluebird weekdays")
    assert text == "No memory found for that question."


def test_the_rate_limit_applies_per_consumer(browser, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "memory_socket_per_minute", 2)
    a, b = add(owner, "A"), add(owner, "B")
    assert [rpc(browser, a, "ping").status_code for _ in range(3)] == [200, 200, 429]
    assert rpc(browser, b, "ping").status_code == 200


def test_the_owner_sees_every_query_in_the_access_log(browser, owner, facts):
    token = add(owner, "Claude", "owner")
    ask(browser, token, "team offsite Lisbon")
    log = owner.get("/app/memory-socket").json()["log"]
    assert log and log[0]["client"] == "Claude" and log[0]["scope"] == "owner" and log[0]["returned"] >= 1
    assert "Lisbon" not in str(log)  # the log records that it was asked, not what was returned


def test_owner_routes_refuse_everyone_else(browser, owner):
    assert browser().get("/app/memory-socket").status_code == 401
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.get("/app/memory-socket").status_code == 403
    assert browser().post("/app/memory-socket/clients", json={"name": "x"}).status_code in (401, 403)
