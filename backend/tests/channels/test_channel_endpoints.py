"""ADR 0009 T4: each owner's own Telegram bot is a channel endpoint of their Ava.

A bot serves one Ava; messages are routed by the bot before anything else: a poller runs
inside its owner's instance, and a webhook names its endpoint and proves it with that
endpoint's own secret.
"""

from __future__ import annotations

import base64
import threading
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.app import connections
from superaide.channels import runner as runner_mod
from superaide.channels import telegram
from superaide.identity.vault import crypto, store
from superaide.main import create_app
from superaide.platform.config import get_settings
from superaide.platform.db.models import Event
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy import endpoints
from superaide.platform.tenancy.context import InstanceScope, current_scope, use_instance
from superaide.platform.tenancy.models import ChannelEndpoint

A = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
B = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
TOKEN_A = "111111:" + "A" * 35
TOKEN_B = "222222:" + "B" * 35


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


@pytest.fixture
def instances() -> None:
    """Tenant and instance rows for A and B (endpoints point at them)."""
    from superaide.identity.auth.service import Client, signup
    from superaide.platform.tenancy.models import AgentInstance, Tenant

    with session_scope() as s:
        for scope, email in ((A, "a@example.com"), (B, "b@example.com")):
            user, _ = signup(s, email=email, password="correct horse battery", name="Owner", client=Client())
            s.add(Tenant(id=scope.tenant_id))
            s.flush()
            s.add(AgentInstance(id=scope.agent_id, tenant_id=scope.tenant_id, agent_slug="ava", owner_user_id=user.id))


def register(scope: InstanceScope, bot: str) -> ChannelEndpoint:
    with use_instance(scope), session_scope() as s:
        row = endpoints.register_telegram(s, bot)
        s.expunge(row)
        return row


def test_a_bot_serves_one_ava(instances):
    first = register(A, "111111")
    assert register(A, "111111").id == first.id  # again: the same endpoint
    with pytest.raises(endpoints.EndpointTaken):
        register(B, "111111")
    register(A, "333333")  # A switches bots: the old one is released
    register(B, "111111")
    with session_scope() as s:
        rows = {(r.external_key, r.agent_id) for r in s.scalars(select(ChannelEndpoint))}
    assert rows == {("333333", A.agent_id), ("111111", B.agent_id)}


def test_each_endpoint_has_its_own_webhook_secret(instances):
    a, b = register(A, "111111"), register(B, "222222")
    assert endpoints.webhook_secret(a.id) != endpoints.webhook_secret(b.id)
    assert endpoints.secret_ok(a.id, endpoints.webhook_secret(a.id))
    assert not endpoints.secret_ok(a.id, endpoints.webhook_secret(b.id))
    assert not endpoints.secret_ok(a.id, None)


# --- connecting and disconnecting in the app ---------------------------------------------------


def telegram_api(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"username": "some_bot", "first_name": "Ava"}})


def owner(app, name: str, email: str) -> TestClient:
    c = TestClient(app, headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": name, "email": email, "password": "correct horse battery"})
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def test_connecting_a_bot_another_ava_uses_is_refused(agent, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(telegram_api)))
    app = create_app(agent)
    a, b = owner(app, "Jordan", "s@example.com"), owner(app, "Priya", "p@example.com")
    assert a.post("/app/connections/telegram", json={"bot_token": TOKEN_A}).status_code == 200
    r = b.post("/app/connections/telegram", json={"bot_token": TOKEN_A})
    assert r.status_code == 409 and "another Ava" in r.json()["detail"]
    assert b.post("/app/connections/telegram", json={"bot_token": TOKEN_B}).status_code == 200
    with session_scope() as s:
        assert {r.external_key for r in s.scalars(select(ChannelEndpoint))} == {"111111", "222222"}
    assert a.post("/app/connections/telegram/disconnect").status_code == 200
    with session_scope() as s:
        assert {r.external_key for r in s.scalars(select(ChannelEndpoint))} == {"222222"}


# --- routing -----------------------------------------------------------------------------------


def update(text_: str = "hello?", sender: int = 4242) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 7, "date": 0, "text": text_,
            "from": {"id": sender, "is_bot": False, "first_name": "Someone"},
            "chat": {"id": sender, "type": "private"},
        },
    }  # fmt: skip


def test_a_webhook_is_handled_by_its_owners_ava_only(agent, instances):
    a, b = register(A, "111111"), register(B, "222222")
    client = TestClient(create_app(agent))
    secret = endpoints.webhook_secret(a.id)
    for bad in (None, endpoints.webhook_secret(b.id), "x" * 64):
        headers = {telegram.SECRET_HEADER: bad} if bad else {}
        assert client.post(f"/webhooks/telegram/{a.id}", json=update(), headers=headers).status_code == 401
    unknown = uuid.uuid4()
    assert (
        client.post(
            f"/webhooks/telegram/{unknown}", json=update(), headers={telegram.SECRET_HEADER: secret}
        ).status_code
        == 401
    )
    r = client.post(
        f"/webhooks/telegram/{a.id}", json=update("hi from telegram"), headers={telegram.SECRET_HEADER: secret}
    )
    assert r.status_code == 200
    with session_scope() as s:
        rows = s.scalars(select(Event).where(Event.content == "hi from telegram")).all()
        assert rows and {e.agent_id for e in rows} == {A.agent_id}


def test_each_owner_bot_is_polled_inside_its_owners_instance(monkeypatch):
    seen: dict[str, InstanceScope | None] = {}
    done = threading.Event()

    def fake_poll(token, handle, stop):
        seen[token] = current_scope()
        if len(seen) == 2:
            done.set()
        stop.wait(5)

    monkeypatch.setattr(telegram, "poll_forever", fake_poll)
    r = runner_mod.ChannelRunner(lambda msg: None)
    try:
        running = r.ensure_bots({TOKEN_A: A, TOKEN_B: B})
        assert running == ["111111", "222222"]
        assert done.wait(5)
        assert seen == {TOKEN_A: A, TOKEN_B: B}
        assert r.ensure_bots({TOKEN_B: B}) == ["222222"]  # A disconnected: its poller stops
    finally:
        r.stop()


def test_bots_are_found_in_every_instances_own_vault(agent, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    app = create_app(agent)
    owner(app, "Jordan", "s@example.com"), owner(app, "Priya", "p@example.com")
    with session_scope() as s:
        scopes = [
            InstanceScope(tenant_id=t, agent_id=i)
            for t, i in s.execute(text("SELECT tenant_id, id FROM agent_instance ORDER BY created_at")).all()
        ]
    for scope, token in zip(scopes, (TOKEN_A, TOKEN_B), strict=True):
        with use_instance(scope), session_scope() as s:
            store.save(s, "telegram", {"bot_token": token}, user_id=None, meta={})
    assert endpoints.sync_telegram() == {TOKEN_A: scopes[0], TOKEN_B: scopes[1]}
    with session_scope() as s:
        assert {(r.external_key, r.agent_id) for r in s.scalars(select(ChannelEndpoint))} == {
            ("111111", scopes[0].agent_id),
            ("222222", scopes[1].agent_id),
        }


def test_with_a_public_address_each_bot_gets_its_own_webhook(agent, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_webhook_base_url", "https://ava.example.com")
    monkeypatch.setattr(settings, "run_channels_in_api", True)
    monkeypatch.setattr(settings, "run_jobs_in_api", False)
    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(telegram_api)))
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        telegram, "set_webhook", lambda token, url, secret, client=None: calls.append((token, url, secret)) or True
    )
    app = create_app()
    a = owner(app, "Jordan", "s@example.com")
    assert a.post("/app/connections/telegram", json={"bot_token": TOKEN_A}).status_code == 200
    with session_scope() as s:
        [endpoint] = s.scalars(select(ChannelEndpoint)).all()
    assert calls == [
        (TOKEN_A, f"https://ava.example.com/webhooks/telegram/{endpoint.id}", endpoints.webhook_secret(endpoint.id))
    ]
