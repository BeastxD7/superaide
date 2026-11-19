"""MCP tools in the owner's loop, the Settings routes, and isolation between owners (issue #666)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.approvals.models import Approval
from superaide.integrations.mcp import tools
from superaide.integrations.mcp.models import McpServer
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, use_instance
from tests.api_support import OTHER, hire, signup
from tests.pipeline.test_loop import Script, tool_use, turn_for

HTTP = {"label": "Notes", "transport": "http", "url": "https://notes.example.com/mcp"}
SEARCH = "mcp__notes__search"


def scope_of(email: str) -> InstanceScope:
    with session_scope() as s:
        row = s.execute(
            text(
                "SELECT i.tenant_id, i.id FROM agent_instance i JOIN instance_member m ON m.agent_id = i.id "
                "JOIN user_account u ON u.id = m.user_id WHERE u.email_normalized = :e"
            ),
            {"e": email},
        ).one()
        return InstanceScope(tenant_id=row[0], agent_id=row[1])


@pytest.fixture
def owner(browser, calls) -> TestClient:
    c = browser()
    assert signup(c).status_code == 200 and hire(c).status_code == 200
    return c


def add_and_approve(c: TestClient, risk: dict[str, str] | None = None, **body: Any) -> dict[str, Any]:
    made = c.post("/app/mcp/servers", json={**HTTP, **body})
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    assert c.post(f"/app/mcp/servers/{sid}/test").json()["error"] == ""
    assert c.post(f"/app/mcp/servers/{sid}/approve", json={}).status_code == 200
    for tool, cls in (risk or {}).items():
        assert c.put(f"/app/mcp/servers/{sid}/tools/{tool}/risk", json={"risk": cls}).status_code == 200
    return c.get("/app/mcp/servers").json()["servers"][0]


def owner_turn(email: str, script: Script, *, shared: bool = False):
    return use_instance(scope_of(email)), session_scope(), script, shared


# --- the loop ---------------------------------------------------------------------------------------------------


def test_the_owner_turn_offers_the_approved_tools_and_a_result_taints_the_turn(owner, calls):
    add_and_approve(owner, {"search": "read"})
    script = Script(tool_use(SEARCH, {"query": "lunch"}), "Three notes about lunch.")
    with use_instance(scope_of("jordan@example.com")), session_scope() as s:
        turn = turn_for(s, script)
        assert SEARCH in [t["name"] for t in turn.tools()]
        result = turn.run([{"role": "user", "content": "find my lunch notes"}])
        assert result.tainted is True and "mcp" in result.notes
    assert calls == [("search", {"query": "lunch", "limit": 3})]
    reply = script.seen[-1][-1]["content"][0]["content"]
    assert reply.startswith("[Untrusted output of the MCP tool search")  # the model got it labelled as data


def test_after_an_mcp_result_the_next_call_waits_for_the_owner_instead_of_running(owner, calls):
    add_and_approve(owner, {"search": "read"})
    script = Script(
        tool_use(SEARCH, {"query": "a"}, "t1"),
        tool_use(SEARCH, {"query": "b"}, "t2"),
        "Asked for your OK on the second.",
    )
    with use_instance(scope_of("jordan@example.com")), session_scope() as s:
        turn_for(s, script).run([{"role": "user", "content": "x"}])
        pending = s.scalars(select(Approval).where(Approval.kind == tools.KIND)).all()
        assert len(pending) == 1 and pending[0].payload["args"] == {"query": "b"}
    assert [c[1]["query"] for c in calls] == ["a"]  # the injected follow-up never ran by itself


def test_a_shared_room_never_gets_or_runs_mcp_tools(owner, calls):
    add_and_approve(owner, {"search": "read"})
    script = Script(tool_use(SEARCH, {"query": "a"}), "No.")
    with use_instance(scope_of("jordan@example.com")), session_scope() as s:
        turn = turn_for(s, script)
        turn.shared = True
        assert SEARCH not in [t["name"] for t in turn.tools()]
        turn.run([{"role": "user", "content": "x"}])  # the model asks for it anyway
    assert calls == []


def test_a_non_owner_sender_gets_no_mcp_tools(owner, calls):
    add_and_approve(owner, {"search": "read"})
    with use_instance(scope_of("jordan@example.com")), session_scope() as s:
        turn = turn_for(s, Script("ok"))
        turn.sender.person.relation = "colleague"
        assert turn.sender.relation == "colleague"
        assert SEARCH not in [t["name"] for t in turn.tools()]
        assert turn._dispatch(SEARCH, {"query": "a"})[1] is True  # noqa: SLF001
    assert calls == []


# --- the routes ----------------------------------------------------------------------------------------------------


def test_the_list_never_carries_a_secret_and_shows_each_tool_with_its_class(owner, calls):
    canary = "Bearer sk-canary-aaaabbbbccccdddd"
    server = add_and_approve(owner, {"search": "read"}, headers={"Authorization": canary})
    body = owner.get("/app/mcp/servers").text
    assert canary not in body and "sk-canary" not in body
    assert server["secrets"]["headers"]["Authorization"].endswith("dddd")
    by_name = {t["name"]: t for t in server["tools"]}
    assert by_name["search"]["risk"] == "read" and by_name["search"]["approval"] == "approved"
    assert by_name["create_note"]["risk"] == "write" and by_name["delete_note"]["risk"] == "destructive"


def test_a_program_server_needs_a_step_up_confirmation(owner, calls):
    body = {"label": "Files", "transport": "stdio", "command": "npx", "args": ["-y", "some-server"]}
    assert owner.post("/app/mcp/servers", json=body).status_code == 403
    nonce = owner.post("/app/stepup", json={"action": "mcp_stdio_server"}).json()["nonce"]
    made = owner.post("/app/mcp/servers", json={**body, "confirmation": nonce})
    assert made.status_code == 201 and made.json()["command"] == "npx"
    assert owner.post("/app/mcp/servers", json={**body, "confirmation": nonce}).status_code == 403  # a nonce works once


def test_bad_input_is_refused_not_a_500(owner, calls):
    assert owner.post("/app/mcp/servers", json={**HTTP, "url": "http://169.254.169.254/x"}).status_code == 422
    assert owner.post("/app/mcp/servers", json={**HTTP, "headers": {"bad name": "x"}}).status_code == 422
    assert owner.post("/app/mcp/servers", json={"label": "", "transport": "http"}).status_code == 422
    assert owner.post("/app/mcp/servers", json={**HTTP, "transport": "carrier-pigeon"}).status_code == 422
    sid = add_and_approve(owner)["id"]
    assert owner.put(f"/app/mcp/servers/{sid}/tools/nope/risk", json={"risk": "read"}).status_code == 404
    assert owner.put(f"/app/mcp/servers/{sid}/tools/search/risk", json={"risk": "yolo"}).status_code == 422
    assert owner.post(f"/app/mcp/servers/{sid}/approve", json={"tools": ["ghost"]}).status_code == 422
    assert owner.patch(f"/app/mcp/servers/{sid}", json={"timeout_s": 9999}).status_code == 422
    assert owner.get("/app/mcp/servers/not-a-uuid").status_code in {404, 405}


def test_a_failed_test_is_reported_and_kept_on_the_server(owner, monkeypatch):
    from superaide.integrations.mcp import transport

    def refuse(target, home):
        raise transport.McpError("The server could not be used (ConnectError).")

    monkeypatch.setattr(transport, "FACTORY", refuse)
    sid = owner.post("/app/mcp/servers", json=HTTP).json()["id"]
    out = owner.post(f"/app/mcp/servers/{sid}/test").json()
    assert "could not be used" in out["error"] and out["tools"] == []
    assert owner.get("/app/mcp/servers").json()["servers"][0]["last_error"] == out["error"]


def test_removing_and_switching_off(owner, calls):
    server = add_and_approve(owner, {"search": "read"})
    assert owner.patch(f"/app/mcp/servers/{server['id']}", json={"enabled": False}).json()["enabled"] is False
    with use_instance(scope_of("jordan@example.com")), session_scope() as s:
        assert tools.offered(s) == []
    assert owner.delete(f"/app/mcp/servers/{server['id']}").status_code == 204
    assert owner.get("/app/mcp/servers").json()["servers"] == []


# --- isolation: one owner's servers are never another's -------------------------------------------------------------


def test_one_owner_never_sees_or_uses_another_owners_server(browser, owner, calls):
    mine = add_and_approve(owner, {"search": "read"}, headers={"Authorization": "Bearer sk-aaaa1111bbbb2222"})
    other = browser()
    assert signup(other, **OTHER).status_code == 200 and hire(other).status_code == 200
    assert other.get("/app/mcp/servers").json()["servers"] == []
    for method, path, body in (
        ("patch", f"/app/mcp/servers/{mine['id']}", {"enabled": False}),
        ("delete", f"/app/mcp/servers/{mine['id']}", None),
        ("post", f"/app/mcp/servers/{mine['id']}/test", None),
        ("post", f"/app/mcp/servers/{mine['id']}/approve", {}),
        ("put", f"/app/mcp/servers/{mine['id']}/tools/search/risk", {"risk": "read"}),
    ):
        r = getattr(other, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, path, r.text)
    # her Ava is not offered his tools, and could not run them even if asked by name
    with use_instance(scope_of("priya@example.com")), session_scope() as s:
        assert tools.offered(s) == [] and tools.resolve(s, SEARCH) is None
        assert s.scalars(select(McpServer)).all() == []
    assert owner.get("/app/mcp/servers").json()["servers"][0]["enabled"] is True  # his is untouched
    assert calls == []


def test_tools_asked_after_the_turns_session_closed_do_not_reopen_it(owner, calls):
    """Regression: tools() can run after the turn's session closed; reading the servers on it left a transaction open,
    which blocked the next TRUNCATE (and a real table lock). It reads with its own short session instead."""
    add_and_approve(owner, {"search": "read"})
    with use_instance(scope_of("jordan@example.com")), session_scope() as s:
        turn = turn_for(s, Script("ok"))
    with use_instance(scope_of("jordan@example.com")):
        assert SEARCH in [t["name"] for t in turn.tools()]
    from superaide.platform.db.session import get_engine

    with get_engine().begin() as conn:  # would wait forever if the closed session had reopened a transaction on it
        conn.execute(text("SET LOCAL lock_timeout = '3s'"))
        conn.execute(text("TRUNCATE mcp_server CASCADE"))
