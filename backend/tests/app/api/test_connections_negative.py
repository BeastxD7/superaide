"""Connections over HTTP when the credential is wrong: nothing stored, nothing sent, nothing echoed.

The services are the fakes from test_connections (an httpx MockTransport); each
test also records whether a request was made at all.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import func, select

from superaide.app import connections
from superaide.db.models import AuditLog, IntegrationSecret
from superaide.db.session import session_scope
from tests.api_support import app_routes
from tests.app.test_connections import CLAUDE_KEY, TG_TOKEN, services

SLACK_BOT = "xoxb-1111-2222-abcdefghijklmnop"
SLACK_APP = "xapp-1-A111-2222-abcdefghijklmnop"


@pytest.fixture
def network(monkeypatch) -> list[str]:
    """The fake Anthropic/Telegram/Slack; returns the URLs that were requested."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return services(request)

    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def _stored() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(IntegrationSecret)) or 0


def _connected_audits() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "connected")) or 0


@pytest.mark.parametrize(
    ("path", "body", "message"),
    [
        ("telegram", {"bot_token": "not-a-token-at-all"}, "doesn't look like a bot token"),
        ("telegram", {"bot_token": "12345:" + "A" * 29}, "doesn't look like a bot token"),
        ("telegram", {"bot_token": "123456789:" + "A" * 35 + "/../x"}, "doesn't look like a bot token"),
        ("telegram", {"bot_token": "abcdefghi:" + "A" * 35}, "doesn't look like a bot token"),
        ("slack", {"bot_token": "xoxp-user-token-123", "app_token": SLACK_APP}, "should start with xoxb-"),
        ("slack", {"bot_token": SLACK_BOT, "app_token": "xoxb-not-an-app-token"}, "should start with xapp-"),
    ],
)
def test_malformed_tokens_are_refused_without_any_network_call(owner, network, path, body, message):
    r = owner.post(f"/app/connections/{path}", json=body)
    assert r.status_code == 400 and message in r.json()["detail"], r.text
    assert network == [] and _stored() == 0 and _connected_audits() == 0
    for value in body.values():
        assert value.strip() not in r.text


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("telegram", {"bot_token": None}),
        pytest.param("telegram", {"bot_token": "1" * 101}, id="telegram-too-long"),
        ("slack", {"bot_token": SLACK_BOT}),
        ("slack", {"app_token": SLACK_APP}),
        pytest.param("slack", {"bot_token": SLACK_BOT, "app_token": "xapp-" + "a" * 300}, id="slack-too-long"),
    ],
)
def test_malformed_bodies_are_422_and_never_echo_the_value(owner, network, path, body):
    r = owner.post(f"/app/connections/{path}", json=body)
    assert r.status_code == 422
    for value in body.values():
        assert str(value) not in r.text, "a credential was echoed back in the validation error"
    assert network == [] and _stored() == 0


@pytest.mark.parametrize(
    ("path", "body", "message"),
    [
        ("telegram", {"bot_token": "987654321:" + "B" * 35}, "didn't accept that token"),
    ],
)
def test_a_credential_the_service_rejects_is_400_and_not_stored(owner, network, path, body, message):
    r = owner.post(f"/app/connections/{path}", json=body)
    assert r.status_code == 400 and message in r.json()["detail"]
    assert len(network) == 1  # one cheap check, nothing more
    assert _stored() == 0 and _connected_audits() == 0
    for value in body.values():
        assert value not in r.text
    assert owner.get("/app/connections").json()["services"][path]["connected"] is False


@pytest.mark.parametrize(
    ("fails", "message"),
    [("auth.test", "didn't accept the bot token"), ("apps.connections.open", "didn't accept the app-level token")],
)
def test_slack_with_one_bad_token_stores_neither(owner, monkeypatch, fails, message):
    def handler(request: httpx.Request) -> httpx.Response:
        if fails in str(request.url):
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        return services(request)

    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    r = owner.post("/app/connections/slack", json={"bot_token": SLACK_BOT, "app_token": SLACK_APP})
    assert r.status_code == 400 and message in r.json()["detail"]
    assert _stored() == 0
    assert SLACK_BOT not in r.text and SLACK_APP not in r.text


def _network_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down", request=request)


TELEGRAM = ("telegram", {"bot_token": TG_TOKEN})
SLACK = ("slack", {"bot_token": SLACK_BOT, "app_token": SLACK_APP})


@pytest.mark.parametrize(
    ("service", "respond", "message"),
    [
        (TELEGRAM, _network_down, "Couldn't reach Telegram"),
        (TELEGRAM, lambda r: httpx.Response(502, content=b"<html>Bad gateway</html>"), "Couldn't reach Telegram"),
        (TELEGRAM, lambda r: httpx.Response(200, json={"ok": False}), "didn't accept that token"),
        (SLACK, _network_down, "Couldn't reach Slack"),
        (SLACK, lambda r: httpx.Response(200, content=b"not json"), "Couldn't reach Slack"),
    ],
    ids=[
        "telegram-down",
        "telegram-502-html",
        "telegram-not-ok",
        "slack-down",
        "slack-not-json",
    ],
)
def test_service_failures_are_a_plain_400_and_nothing_is_stored(owner, monkeypatch, service, respond, message):
    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    path, body = service
    r = owner.post(f"/app/connections/{path}", json=body)
    assert r.status_code == 400 and message in r.json()["detail"], r.text
    assert all(value not in r.text for value in body.values())
    assert _stored() == 0


@pytest.mark.parametrize("provider", ["telegram", "slack"])
def test_disconnecting_what_isnt_connected_is_harmless(owner, network, provider):
    r = owner.post(f"/app/connections/{provider}/disconnect")
    assert r.status_code == 200 and r.json()["services"][provider]["connected"] is False
    assert network == []


@pytest.mark.parametrize("provider", ["whatsapp", "github", "anthropic", "ANTHROPIC", "..", " telegram"])
def test_disconnecting_an_unknown_service_is_404(owner, provider):
    assert owner.post(f"/app/connections/{provider}/disconnect").status_code == 404


def test_disconnect_removes_only_that_service(owner, network):
    owner.post("/app/connections/slack", json={"bot_token": SLACK_BOT, "app_token": SLACK_APP})
    owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN})
    r = owner.post("/app/connections/telegram/disconnect").json()["services"]
    assert r["telegram"]["connected"] is False and r["slack"]["connected"] is True
    assert owner.post("/app/connections/telegram/disconnect").status_code == 200  # twice is fine


def test_secrets_never_appear_in_any_response(app, owner, network, browser):
    """Connect everything, then read every JSON endpoint the owner and the operator have."""
    secrets = [TG_TOKEN, SLACK_BOT, SLACK_APP]
    responses = [
        owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN}),
        owner.post("/app/connections/slack", json={"bot_token": SLACK_BOT, "app_token": SLACK_APP}),
        owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN}),  # again, same token
    ]
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    for method, path in [r for r in _get_routes(app) if "{" not in r[1]]:
        responses.append(owner.request(method, path))
        responses.append(owner.request(method, path, headers={"Authorization": "Bearer test-admin-token"}))
    responses.append(owner.post("/app/connections/telegram/disconnect"))
    for r in responses:
        assert r.status_code < 500, (r.request.url, r.text)
        for secret in secrets:
            assert secret not in r.text, (r.request.url, "leaked a credential")
            assert secret[10:-4] not in r.text, (r.request.url, "leaked most of a credential")
    # The masks are recognisable but useless.
    hints = owner.get("/app/connections").json()["services"]
    for service in ("telegram", "slack"):
        for hint in hints[service]["hints"].values():
            assert len(hint) <= 13
    # Nor in what's kept on disk (the audit log) beyond the ciphertext.
    with session_scope() as s:
        audit_text = json.dumps([a.detail for a in s.scalars(select(AuditLog))])
        ciphertexts = [row.ciphertext for row in s.scalars(select(IntegrationSecret))]
    assert all(secret not in audit_text for secret in secrets)
    assert all(secret not in c for secret in secrets for c in ciphertexts)


def _get_routes(app) -> list[tuple[str, str]]:
    return [r for prefix in ("/app", "/auth", "/api") for r in app_routes(app, prefix) if r[0] == "GET"]


def test_there_is_no_claude_connection_here(owner, network):
    """The AI model is connected only through /app/providers (the AI model page), never on Connections."""
    assert owner.post("/app/connections/anthropic", json={"api_key": CLAUDE_KEY}).status_code in (404, 405)
    assert "anthropic" not in owner.get("/app/connections").json()["services"]
    assert "anthropic" not in owner.get("/app/connections").json()["guides"]
    assert network == [] and _stored() == 0
