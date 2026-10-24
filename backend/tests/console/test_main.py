"""The API app itself: health, webhook gates, the simulator switch, CORS and startup/shutdown.

tests/console/test_api.py covers the signature helpers, the happy-path webhooks and the
simulator's impostor scenario; this file covers the switches around them.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from superaide.channels.telegram import SECRET_HEADER
from superaide.db.models import AuditLog, Event
from superaide.db.session import session_scope
from tests.conftest import COLLEAGUE_SLACK, MANAGER_SLACK, MANAGER_TG, OUR_TEAM

# `superaide.main` the module; the package's own `main` attribute is the CLI entry point.
main = importlib.import_module("superaide.main")
AUTH = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def configure(monkeypatch, settings):
    """Build apps with changed settings, without touching the cached shared Settings object."""

    def apply(**changes):
        changed = settings.model_copy(update=changes)
        monkeypatch.setattr(main, "get_settings", lambda: changed)
        return changed

    return apply


@pytest.fixture
def client(agent):
    return TestClient(main.create_app(agent))


def _slack_headers(body: bytes, secret: str = "test-slack-secret", ts: int | None = None) -> dict[str, str]:
    ts = ts or int(time.time())
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": sig, "Content-Type": "application/json"}


def _rejections() -> list[dict]:
    with session_scope() as s:
        return [a.detail for a in s.scalars(select(AuditLog).where(AuditLog.action == "rejected"))]


def _events() -> int:
    with session_scope() as s:
        return int(s.scalar(select(func.count()).select_from(Event)) or 0)


def _tg_update(sender: int, text: str = "hi", *, is_bot: bool = False) -> dict:
    return {
        "update_id": 9,
        "message": {
            "message_id": 1,
            "from": {"id": sender, "is_bot": is_bot, "first_name": "S"},
            "chat": {"id": sender, "type": "private"},
            "text": text,
        },
    }


def _slack_event(user: str, text: str = "hi", **event) -> bytes:
    payload = {
        "type": "event_callback",
        "team_id": OUR_TEAM,
        "event": {"type": "message", "channel_type": "im", "channel": f"D_{user}", "user": user, "text": text} | event,
    }
    return json.dumps(payload).encode()


# --- basics ---------------------------------------------------------------------------


def test_health_and_index(client):
    assert client.get("/health").json() == {"status": "ok"}
    index = client.get("/").json()
    assert index["service"] == "superaide API" and index["docs"] == "/docs" and index["frontend"].startswith("http")


# --- webhooks -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {SECRET_HEADER: ""}, {SECRET_HEADER: "test-telegram-secret "}, {SECRET_HEADER: "TEST-TELEGRAM-SECRET"}],
    ids=["missing", "empty", "trailing-space", "wrong-case"],
)
def test_telegram_webhook_refuses_unverified_requests_and_audits_them(client, model, manager, headers):
    r = client.post("/webhooks/telegram", json=_tg_update(int(MANAGER_TG)), headers=headers)
    assert r.status_code == 401
    assert _rejections() == [{"provider": "telegram", "reason": "bad secret"}]
    assert _events() == 0 and model.systems == []  # the message never reached the agent


def test_telegram_webhook_is_closed_when_no_secret_is_configured(configure, agent, model, manager):
    configure(telegram_webhook_secret="")
    c = TestClient(main.create_app(agent))
    for secret in ["", "anything"]:
        assert (
            c.post("/webhooks/telegram", json=_tg_update(int(MANAGER_TG)), headers={SECRET_HEADER: secret}).status_code
            == 401
        )
    assert _events() == 0 and model.systems == []


def test_telegram_webhook_accepts_but_ignores_non_messages(client, model, manager):
    ok = {SECRET_HEADER: "test-telegram-secret"}
    assert client.post("/webhooks/telegram", json=_tg_update(int(MANAGER_TG), is_bot=True), headers=ok).json() == {
        "ok": True
    }
    assert client.post("/webhooks/telegram", json={"update_id": 3, "edited_message": {}}, headers=ok).json() == {
        "ok": True
    }
    assert _events() == 0 and model.systems == []


def test_verified_telegram_message_reaches_the_agent(client, model, manager, outbox):
    r = client.post(
        "/webhooks/telegram", json=_tg_update(int(MANAGER_TG), "hello"), headers={SECRET_HEADER: "test-telegram-secret"}
    )
    assert r.json() == {"ok": True}
    assert len(model.systems) == 1 and outbox["telegram"].sent  # handled in the background, replied on Telegram


@pytest.mark.parametrize(
    "make_headers",
    [
        lambda body: {"Content-Type": "application/json"},
        lambda body: _slack_headers(body, secret="someone-elses-secret"),
        lambda body: _slack_headers(body, ts=int(time.time()) - 600),  # a replayed request
        lambda body: _slack_headers(b"{}"),  # signed for a different body
        lambda body: {**_slack_headers(body), "X-Slack-Request-Timestamp": "not-a-number"},
    ],
    ids=["unsigned", "wrong-secret", "stale", "other-body", "bad-timestamp"],
)
def test_slack_webhook_refuses_unverified_requests_and_audits_them(client, model, manager, make_headers):
    body = _slack_event(MANAGER_SLACK)
    r = client.post("/webhooks/slack", content=body, headers=make_headers(body))
    assert r.status_code == 401
    assert _rejections() == [{"provider": "slack", "reason": "bad signature"}]
    assert _events() == 0 and model.systems == []


def test_slack_webhook_is_closed_when_no_signing_secret_is_configured(configure, agent, model, manager):
    configure(slack_signing_secret="")
    c = TestClient(main.create_app(agent))
    body = _slack_event(MANAGER_SLACK)
    assert c.post("/webhooks/slack", content=body, headers=_slack_headers(body, secret="")).status_code == 401
    assert _events() == 0


def test_verified_slack_message_reaches_the_agent(client, model, colleague, outbox):
    body = _slack_event(COLLEAGUE_SLACK, "is Jordan free at 3?")
    r = client.post("/webhooks/slack", content=body, headers=_slack_headers(body))
    assert r.json() == {"ok": True}
    assert len(model.systems) == 1 and "Priya Tan" in model.systems[0] and outbox["slack"].sent


def test_verified_slack_events_that_arent_messages_are_ignored(client, model, manager):
    for body in [
        _slack_event(MANAGER_SLACK, bot_id="B1"),
        _slack_event(MANAGER_SLACK, subtype="message_changed"),
        _slack_event(MANAGER_SLACK, channel_type="channel"),  # in channels only @-mentions count
        json.dumps({"type": "event_callback", "event": {"type": "reaction_added", "user": MANAGER_SLACK}}).encode(),
    ]:
        assert client.post("/webhooks/slack", content=body, headers=_slack_headers(body)).json() == {"ok": True}
    assert _events() == 0 and model.systems == []


# --- simulator ------------------------------------------------------------------------

SIM = {"provider": "telegram", "sender_id": "777", "text": "hello"}


def test_simulator_needs_the_admin_token(client):
    assert client.post("/api/simulate", json=SIM).status_code == 401
    assert client.post("/api/simulate", json=SIM, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/simulate", json=SIM, headers={"Authorization": "test-admin-token-x"}).status_code == 401
    assert _events() == 0


def test_simulator_is_off_unless_enabled(configure, agent):
    configure(enable_simulator=False)
    c = TestClient(main.create_app(agent))
    r = c.post("/api/simulate", json=SIM, headers=AUTH)
    assert r.status_code == 403 and "ENABLE_SIMULATOR" in r.json()["detail"]
    assert _events() == 0
    assert c.post("/api/simulate", json=SIM).status_code == 401  # the token is checked first


def test_simulator_is_off_without_an_admin_token(monkeypatch, settings, agent):
    from superaide.api import admin

    monkeypatch.setattr(admin, "get_settings", lambda: settings.model_copy(update={"admin_token": ""}))
    c = TestClient(main.create_app(agent))
    assert c.post("/api/simulate", json=SIM, headers={"Authorization": "Bearer "}).status_code == 503
    assert _events() == 0


def test_simulator_captures_replies_instead_of_sending_them(client, manager, outbox):
    sim = {"provider": "slack", "sender_id": MANAGER_SLACK, "tenant_scope": OUR_TEAM, "text": "hello"}
    r = client.post("/api/simulate", json=sim, headers=AUTH)
    data = r.json()
    assert r.status_code == 200 and data["mode"] == "known" and data["reply"] == "ok"
    assert outbox["slack"].sent == [] and outbox["telegram"].sent == []  # the real messengers were not used
    with session_scope() as s:
        audited = s.scalar(select(AuditLog).where(AuditLog.action == "simulated_message"))
        assert audited.detail == {"provider": "slack", "mode": "known"}


# --- CORS -----------------------------------------------------------------------------


def _preflight(c: TestClient, origin: str):
    return c.options("/app/me", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})


def test_cors_allows_only_the_frontend_origin(configure, agent):
    configure(frontend_origin="http://localhost:3000")
    c = TestClient(main.create_app(agent))
    for origin in ["http://localhost:3000", "http://127.0.0.1:3000"]:  # both spellings of the same machine
        r = c.get("/health", headers={"Origin": origin})
        assert r.headers["access-control-allow-origin"] == origin
        assert r.headers["access-control-allow-credentials"] == "true"
        assert _preflight(c, origin).status_code == 200
    for origin in ["https://evil.example", "http://localhost:3001", "http://localhost:3000.evil.example", "null"]:
        r = c.get("/health", headers={"Origin": origin})
        assert "access-control-allow-origin" not in r.headers
        assert _preflight(c, origin).status_code == 400


def test_cors_follows_a_configured_origin(configure, agent):
    configure(frontend_origin="https://app.superaide.example")
    c = TestClient(main.create_app(agent))
    ok = c.get("/health", headers={"Origin": "https://app.superaide.example"})
    assert ok.headers["access-control-allow-origin"] == "https://app.superaide.example"
    for origin in ["http://localhost:3000", "http://127.0.0.1:3000"]:
        assert "access-control-allow-origin" not in c.get("/health", headers={"Origin": origin}).headers


def test_cors_preflight_refuses_other_methods_and_headers(configure, agent):
    configure(frontend_origin="http://localhost:3000")
    c = TestClient(main.create_app(agent))
    origin = {"Origin": "http://localhost:3000"}
    assert c.options("/app/me", headers=origin | {"Access-Control-Request-Method": "DELETE"}).status_code == 400
    r = c.options(
        "/app/me",
        headers=origin | {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Evil"},
    )
    assert r.status_code == 400


# --- startup and shutdown -------------------------------------------------------------


class SpyRunner:
    instances: list[SpyRunner] = []

    def __init__(self, handle) -> None:
        self.handle = handle
        self.ensured: list = []
        self.stopped = 0
        self.fail = False
        SpyRunner.instances.append(self)

    def ensure(self, settings):
        if self.fail:
            raise RuntimeError("socket mode is down")
        self.ensured.append(settings)
        return {"telegram": bool(settings.telegram_bot_token), "slack": bool(settings.slack_bot_token)}

    # The listeners per owner (ADR 0009): each owner's Slack app and Telegram bot.
    def ensure_slack_apps(self, apps):
        if self.fail:
            raise RuntimeError("socket mode is down")
        self.ensured.append(apps)
        return []

    def ensure_bots(self, bots):
        return []

    def stop(self) -> None:
        self.stopped += 1


@pytest.fixture
def spy_runner(monkeypatch):
    SpyRunner.instances = []
    monkeypatch.setattr(importlib.import_module("superaide.runtime"), "ChannelRunner", SpyRunner)  # used by main
    return SpyRunner.instances


def test_startup_with_channels_disabled_starts_no_listeners(configure, spy_runner, monkeypatch):
    configure(run_channels_in_api=False)
    built = []
    monkeypatch.setattr("superaide.factory.build_agent", lambda s: built.append(s))
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
    (runner,) = spy_runner
    assert runner.ensured == [] and runner.stopped == 1 and built == []  # no agent built at startup either


def test_startup_with_a_fixed_agent_never_starts_listeners(configure, spy_runner, agent):
    configure(run_channels_in_api=True)
    with TestClient(main.create_app(agent)):
        pass
    assert spy_runner[0].ensured == [] and spy_runner[0].stopped == 1


def test_startup_without_channel_credentials_starts_nothing(configure, agent):
    """The real runner, with channels on but no Telegram or Slack credentials: nothing to listen to."""
    configure(run_channels_in_api=True, telegram_bot_token="", slack_bot_token="", slack_app_token="")
    app = main.create_app()
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
    assert not any(t.name == "telegram-listener" for t in threading.enumerate())


def test_startup_applies_connections_and_shutdown_stops_them(configure, spy_runner):
    configure(run_channels_in_api=True)
    with TestClient(main.create_app()):
        (runner,) = spy_runner
        assert len(runner.ensured) == 1 and runner.stopped == 0
    assert runner.stopped == 1


def test_a_listener_failure_does_not_stop_the_api_starting(configure, spy_runner, monkeypatch, caplog):
    configure(run_channels_in_api=True)
    original = SpyRunner.__init__

    def failing(self, handle):
        original(self, handle)
        self.fail = True

    monkeypatch.setattr(SpyRunner, "__init__", failing)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").json() == {"status": "ok"}
    assert "couldn't start channel listeners" in caplog.text and spy_runner[0].stopped == 1


# --- the agent is rebuilt when connections change ---------------------------------------


def test_agent_is_built_once_and_rebuilt_when_a_connection_changes(configure, spy_runner, monkeypatch):
    import base64

    from superaide.factory import build_agent as real_build
    from superaide.vault import crypto, store

    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    crypto.master_key.cache_clear()
    configure(run_channels_in_api=False)
    built = []

    def counting(s):
        built.append(s)
        return real_build(s)

    monkeypatch.setattr("superaide.factory.build_agent", counting)
    c = TestClient(main.create_app())
    assert c.post("/api/simulate", json=SIM, headers=AUTH).status_code == 200
    assert c.post("/api/simulate", json=SIM, headers=AUTH).status_code == 200
    assert len(built) == 1  # nothing changed: the same agent
    with session_scope() as s:
        store.save(s, "telegram", {"bot_token": "123456789:" + "B" * 35}, user_id=None, meta={})
    assert c.post("/api/simulate", json=SIM, headers=AUTH).status_code == 200
    assert len(built) == 2 and built[1].telegram_bot_token == "123456789:" + "B" * 35
    crypto.master_key.cache_clear()


def test_api_docs_can_be_turned_off_for_a_public_server(configure, agent):
    on = TestClient(main.create_app(agent))
    assert all(on.get(p).status_code == 200 for p in ("/docs", "/redoc", "/openapi.json"))
    configure(enable_api_docs=False)
    off = TestClient(main.create_app(agent))
    assert all(off.get(p).status_code == 404 for p in ("/docs", "/redoc", "/openapi.json"))
    assert off.get("/health").json() == {"status": "ok"}
