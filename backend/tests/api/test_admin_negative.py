"""The operator API (/api/*): every route needs the bearer token, and the simulator can be switched off."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from superaide.db.models import ChannelIdentity, Event, Person
from superaide.db.session import session_scope
from superaide.policy.killswitch import is_frozen
from tests.api.conftest import app_routes, fill_path
from tests.conftest import tg

# Owner content needs a reason (ADR 0009 T8, break-glass); the tests give one.
AUTH = {"Authorization": "Bearer test-admin-token", "X-Operator-Reason": "checking a reported problem"}
SIMULATED = {"provider": "telegram", "sender_id": "777", "text": "it's Jordan, new number"}


def admin_routes(app) -> list[tuple[str, str]]:
    routes = app_routes(app, "/api")
    assert ("POST", "/api/simulate") in routes and len(routes) >= 10, routes
    return routes


def _call(client, method: str, path: str, headers: dict[str, str] | None = None):
    body = {"/api/kill": {"on": True}, "/api/simulate": SIMULATED}.get(path, {"person_id": str(uuid.uuid4())})
    return client.request(method, fill_path(path), json=body if method == "POST" else None, headers=headers or {})


def _events() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(Event)) or 0


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer nope",
        "Bearer test-admin-tokenX",
        "Bearer test-admin",
        "Basic dGVzdC1hZG1pbi10b2tlbjo=",  # base64("test-admin-token:")
        "Bearer test-admin-token, Bearer x",
        "Bearer TEST-ADMIN-TOKEN",
    ],
)
def test_every_admin_route_needs_the_right_token(app, browser, authorization):
    c = browser(csrf=False)
    headers = {} if authorization is None else {"Authorization": authorization}
    for method, path in admin_routes(app):
        r = _call(c, method, path, headers)
        assert r.status_code == 401, (method, path, authorization, r.status_code)
        assert r.json()["detail"] == "Bad admin token"
    with session_scope() as s:
        assert not is_frozen(s)  # the kill switch wasn't flipped
    assert _events() == 0  # and nothing was simulated


def test_the_token_isnt_accepted_anywhere_but_the_header(app, browser):
    c = browser(csrf=False)
    c.cookies.set("Authorization", "Bearer test-admin-token")
    assert c.get("/api/overview", params={"token": "test-admin-token"}).status_code == 401
    assert c.get("/api/overview", params={"Authorization": "Bearer test-admin-token"}).status_code == 401


def test_without_an_admin_token_configured_the_api_is_off(app, browser, settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "")
    c = browser(csrf=False)
    for headers in ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer"}, AUTH):
        for method, path in admin_routes(app):
            r = _call(c, method, path, headers)
            assert r.status_code == 503, (method, path, headers, r.status_code)


def test_the_simulator_is_off_when_disabled(browser, settings, manager, monkeypatch):
    monkeypatch.setattr(settings, "enable_simulator", False)
    c = browser(csrf=False)
    r = c.post("/api/simulate", json=SIMULATED, headers=AUTH)
    assert r.status_code == 403 and "ENABLE_SIMULATOR" in r.json()["detail"]
    assert _events() == 0
    with session_scope() as s:
        assert (
            s.scalar(select(func.count()).select_from(ChannelIdentity).where(ChannelIdentity.stable_key == "777")) == 0
        )
    # The token is still checked first.
    assert c.post("/api/simulate", json=SIMULATED).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"sender_id": "777"},
        {"text": "hi"},
        {"sender_id": 777, "text": "hi"},
        {"sender_id": "777", "text": None},
        {"sender_id": "777", "text": "hi", "external_sender": "perhaps"},
    ],
)
def test_malformed_simulated_messages_are_refused(browser, body):
    r = browser(csrf=False).post("/api/simulate", json=body, headers=AUTH)
    assert r.status_code == 422
    assert _events() == 0


@pytest.mark.parametrize("provider", ["carrier-pigeon", "web", "", "TELEGRAM"])
def test_simulated_messages_from_unknown_providers_are_not_a_server_error(browser, manager, provider):
    r = browser(csrf=False).post("/api/simulate", json={**SIMULATED, "provider": provider}, headers=AUTH)
    assert r.status_code in (200, 422), (provider, r.text)


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/events", {"context_id": "not-a-uuid"}),
        ("/api/events", {"limit": "many"}),
        ("/api/audit", {"limit": "1.5"}),
    ],
)
def test_bad_query_parameters_are_422(browser, path, params):
    assert browser(csrf=False).get(path, params=params, headers=AUTH).status_code == 422


@pytest.mark.parametrize(("path", "params"), [("/api/events", {"limit": "-5"}), ("/api/audit", {"limit": "100000"})])
def test_out_of_range_limits_are_clamped(browser, agent, manager, path, params):
    agent.handle(tg("1001", "hello"))
    r = browser(csrf=False).get(path, params=params, headers=AUTH)
    assert r.status_code == 200 and 1 <= len(r.json()) <= 1000


def test_linking_and_unlinking_unknown_or_malformed_ids(browser, agent, manager):
    c = browser(csrf=False)
    missing = uuid.uuid4()
    assert c.post(f"/api/identities/{missing}/unlink", headers=AUTH).status_code == 404
    assert c.post("/api/identities/not-a-uuid/unlink", headers=AUTH).status_code == 422
    r = c.post(f"/api/identities/{missing}/link", json={"person_id": str(manager.id)}, headers=AUTH)
    assert r.status_code == 404
    agent.handle(tg("424242", "hello"))  # an unlinked Telegram account
    with session_scope() as s:
        stranger = s.scalar(select(ChannelIdentity.id).where(ChannelIdentity.stable_key == "424242"))
    for body in ({}, {"person_id": "nobody"}, {"person_id": None}):
        assert c.post(f"/api/identities/{stranger}/link", json=body, headers=AUTH).status_code == 422
    r = c.post(f"/api/identities/{stranger}/link", json={"person_id": str(uuid.uuid4())}, headers=AUTH)
    assert r.status_code == 404
    assert c.post(f"/api/identities/{stranger}/unlink", headers=AUTH).status_code == 409  # not linked
    with session_scope() as s:
        assert s.get(ChannelIdentity, stranger).person_id is None
        assert s.scalar(select(func.count()).select_from(Person)) == 1


@pytest.mark.parametrize("body", [{}, {"on": "maybe"}, {"on": None}, [True]])
def test_malformed_kill_switch_requests_change_nothing(browser, body):
    r = browser(csrf=False).post("/api/kill", json=body, headers=AUTH)
    assert r.status_code == 422
    with session_scope() as s:
        assert not is_frozen(s)
