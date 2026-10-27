"""Slack per instance (ADR 0009): each owner brings their own Slack app, like their own Telegram bot.

The app is an endpoint of their Ava, keyed "<team>/<app>" as the identity layer binds Slack
links; its Socket Mode connection runs in one process only and its events are handled in
the owner's instance.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.app import connections
from superaide.channels import slack
from superaide.channels.base import InboundMessage
from superaide.channels.runner import ChannelRunner
from superaide.identity.vault import crypto, store
from superaide.main import create_app
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy import endpoints
from superaide.platform.tenancy.context import InstanceScope, current_scope, use_instance
from superaide.platform.tenancy.models import ChannelEndpoint

APP_A = "xapp-1-A0AAAAAAA-1111-" + "a" * 20
APP_B = "xapp-1-A0BBBBBBB-2222-" + "b" * 20
BOT_A, BOT_B = "xoxb-a-" + "1" * 20, "xoxb-b-" + "2" * 20


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


def test_the_endpoint_key_is_the_one_the_identity_layer_binds_links_to():
    key = endpoints.slack_key("T123", APP_A)
    assert key == "T123/A0AAAAAAA"
    assert key == slack.endpoint_of({"team_id": "T123", "api_app_id": "A0AAAAAAA"})
    assert endpoints.slack_key("T123", "not-an-app-token") == "T123"


def slack_api(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "auth.test" in url:
        return httpx.Response(200, json={"ok": True, "team_id": "T123", "team": "Larkfield", "user": "ava"})
    if "apps.connections.open" in url:
        return httpx.Response(200, json={"ok": True, "url": "wss://example"})
    return httpx.Response(404)


def owner(app, name: str, email: str) -> TestClient:
    c = TestClient(app, headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": name, "email": email, "password": "correct horse battery"})
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def test_an_app_serves_one_ava_and_two_owners_in_one_workspace_bring_their_own(agent, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(slack_api)))
    app = create_app(agent)
    a, b = owner(app, "Jordan", "s@example.com"), owner(app, "Priya", "p@example.com")
    assert a.post("/app/connections/slack", json={"bot_token": BOT_A, "app_token": APP_A}).status_code == 200
    taken = b.post("/app/connections/slack", json={"bot_token": BOT_B, "app_token": APP_A})
    assert taken.status_code == 409 and "another Ava" in taken.json()["detail"]
    # Same workspace (T123), their own app: fine.
    assert b.post("/app/connections/slack", json={"bot_token": BOT_B, "app_token": APP_B}).status_code == 200
    with session_scope() as s:
        assert {r.external_key for r in s.scalars(select(ChannelEndpoint))} == {"T123/A0AAAAAAA", "T123/A0BBBBBBB"}
    assert a.post("/app/connections/slack/disconnect").status_code == 200
    with session_scope() as s:
        assert {r.external_key for r in s.scalars(select(ChannelEndpoint))} == {"T123/A0BBBBBBB"}


def _scopes() -> list[InstanceScope]:
    with session_scope() as s:
        rows = s.execute(text("SELECT tenant_id, id FROM agent_instance ORDER BY created_at")).all()
    return [InstanceScope(tenant_id=t, agent_id=i) for t, i in rows]


def test_every_owners_app_is_found_in_their_own_vault(agent, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    app = create_app(agent)
    owner(app, "Jordan", "s@example.com"), owner(app, "Priya", "p@example.com")
    sa, sb = _scopes()
    for scope, bot, app_token in ((sa, BOT_A, APP_A), (sb, BOT_B, APP_B)):
        with use_instance(scope), session_scope() as s:
            store.save(s, "slack", {"bot_token": bot, "app_token": app_token, "team_id": "T123"}, user_id=None, meta={})
    assert endpoints.sync_slack() == {APP_A: (sa, BOT_A, "T123"), APP_B: (sb, BOT_B, "T123")}


class FakeHandler:
    made: list[FakeHandler] = []

    def __init__(self, bot: str, app: str, team: str, handle: Any) -> None:
        self.app, self.handle, self.closed = app, handle, False
        FakeHandler.made.append(self)

    def connect(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_each_owners_app_listens_once_and_its_events_are_that_owners(monkeypatch):
    FakeHandler.made = []
    monkeypatch.setattr(slack, "socket_mode_handler", FakeHandler)
    a = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    b = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
    seen: list[InstanceScope | None] = []
    first = ChannelRunner(lambda msg: seen.append(current_scope()))
    second = ChannelRunner(lambda msg: None)
    apps = {APP_A: (a, BOT_A, "T123"), APP_B: (b, BOT_B, "T123")}
    try:
        assert first.ensure_slack_apps(apps) == ["T123/A0AAAAAAA", "T123/A0BBBBBBB"]
        assert second.ensure_slack_apps(apps) == []  # both apps listen in the first process
        msg = InboundMessage(
            provider="slack",
            tenant_scope="T123",
            sender_id="U1",
            chat_id="D1",
            chat_kind="dm",
            text="hi",
            verified=True,
        )
        by_app = {h.app: h for h in FakeHandler.made}
        by_app[APP_B].handle(msg)  # Slack calls back on its own thread, with no scope of ours
        by_app[APP_A].handle(msg)
        assert seen == [b, a]
        first.ensure_slack_apps({APP_B: apps[APP_B]})  # A disconnected
        assert by_app[APP_A].closed and not by_app[APP_B].closed
        assert second.ensure_slack_apps(apps) == ["T123/A0AAAAAAA"]  # the freed app moves over
    finally:
        first.stop()
        second.stop()
