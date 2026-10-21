"""Connections: credentials are checked, encrypted, masked, owner-only and applied."""

from __future__ import annotations

import base64
import json
import os
import stat

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agi_agent.app import connections
from agi_agent.db.models import AuditLog, IntegrationSecret
from agi_agent.db.session import session_scope
from agi_agent.main import create_app
from agi_agent.vault import crypto
from agi_agent.vault.store import runtime_settings

PASSWORD = "correct horse battery"
CLAUDE_KEY = "sk-ant-api03-" + "x" * 80 + "4f2a"
TG_TOKEN = "123456789:" + "A" * 35


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


def services(request: httpx.Request) -> httpx.Response:
    """Pretend Anthropic, Telegram and Slack."""
    url = str(request.url)
    if "api.anthropic.com" in url:
        ok = request.headers.get("x-api-key") == CLAUDE_KEY
        return httpx.Response(200 if ok else 401, json={"data": []})
    if "api.telegram.org" in url:
        ok = TG_TOKEN in url
        return httpx.Response(200, json={"ok": ok, "result": {"username": "ava_for_shashank_bot", "first_name": "Ava"}})
    if "auth.test" in url:
        return httpx.Response(200, json={"ok": True, "team_id": "T123", "team": "PebbleRoad", "user": "ava"})
    if "apps.connections.open" in url:
        return httpx.Response(200, json={"ok": True, "url": "wss://example"})
    return httpx.Response(404)


@pytest.fixture
def owner(agent, monkeypatch):
    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(services)))
    app = create_app(agent)
    c = TestClient(app, headers={"X-Requested-With": "agi-agent"})
    c.post("/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": PASSWORD})
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    c.app_ = app
    return c


def test_secrets_are_encrypted_bound_to_their_slot_and_masked():
    token = crypto.encrypt(CLAUDE_KEY, "anthropic/api_key")
    assert CLAUDE_KEY not in token and crypto.decrypt(token, "anthropic/api_key") == CLAUDE_KEY
    with pytest.raises(crypto.VaultError):
        crypto.decrypt(token, "telegram/bot_token")  # can't be moved to another slot
    assert crypto.mask(CLAUDE_KEY) == "sk-…4f2a" and CLAUDE_KEY[8:-4] not in crypto.mask(CLAUDE_KEY)


def test_key_file_fallback_is_private(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRETS_MASTER_KEY")
    monkeypatch.setattr(crypto, "_from_keyring", lambda: None)
    path = tmp_path / "master.key"
    monkeypatch.setenv("AGI_AGENT_KEY_FILE", str(path))
    crypto.master_key.cache_clear()
    key, where = crypto.master_key()
    assert where == "key file" and len(key) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    os.chmod(path, 0o644)
    crypto.master_key.cache_clear()
    with pytest.raises(crypto.VaultError):
        crypto.master_key()


def test_connecting_claude_checks_encrypts_and_never_echoes_the_key(owner):
    bad = owner.post("/app/connections/anthropic", json={"api_key": "sk-ant-wrong-key-123456"})
    assert bad.status_code == 400 and "didn't accept" in bad.json()["detail"]
    r = owner.post("/app/connections/anthropic", json={"api_key": CLAUDE_KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["services"]["anthropic"] | {"since": None} == {
        "connected": True, "via": "app", "hints": {"api_key": "sk-…4f2a"}, "meta": {}, "since": None,
    }  # fmt: skip
    assert CLAUDE_KEY not in r.text and CLAUDE_KEY not in owner.get("/app/connections").text
    with session_scope() as s:
        row = s.scalar(select(IntegrationSecret))
        assert CLAUDE_KEY not in row.ciphertext
        assert all(CLAUDE_KEY not in json.dumps(a.detail) for a in s.scalars(select(AuditLog)))
    # The agent's settings now carry the key (applied without a restart).
    assert runtime_settings().anthropic_api_key == CLAUDE_KEY


def test_telegram_and_slack_connect_and_disconnect(owner):
    assert owner.post("/app/connections/telegram", json={"bot_token": "not-a-token-at-all"}).status_code == 400
    r = owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN}).json()
    telegram = r["services"]["telegram"]
    assert telegram["connected"] and telegram["meta"]["username"] == "ava_for_shashank_bot"
    slack = {"bot_token": "xoxb-1-abc-defghijk", "app_token": "xapp-1-abc-defghi"}
    r = owner.post("/app/connections/slack", json=slack)
    assert r.json()["services"]["slack"]["connected"]
    assert runtime_settings().slack_team_id == "T123"
    assert "manifest_yaml=" in r.json()["guides"]["slack"]["url"]
    r = owner.post("/app/connections/telegram/disconnect").json()
    assert r["services"]["telegram"]["connected"] is False
    assert r["services"]["whatsapp"]["available"] is False


def test_only_the_owner_can_see_or_change_connections(owner, agent):
    other = TestClient(owner.app_, headers={"X-Requested-With": "agi-agent"})
    assert other.get("/app/connections").status_code == 401
    other.post("/auth/signup", json={"name": "Priya Tan", "email": "p@example.com", "password": PASSWORD})
    assert other.get("/app/connections").status_code == 403
    assert other.post("/app/connections/anthropic", json={"api_key": CLAUDE_KEY}).status_code == 403
