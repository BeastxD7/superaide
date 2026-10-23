"""ADR 0009 T3: each hired Ava has its own credentials, freeze switch and runtime.

Owners bring their own model keys (decided 2026-10-22): on a hosted server an owner's Ava
never uses the server's .env keys, and never another owner's.
"""

from __future__ import annotations

import base64
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.app import connections
from superaide.config import get_settings
from superaide.db.models import IntegrationSecret
from superaide.db.session import session_scope
from superaide.factory import brain_status
from superaide.main import create_app
from superaide.policy.killswitch import is_frozen, set_frozen, set_platform_frozen
from superaide.tenancy.context import InstanceScope, use_instance
from superaide.tenancy.models import AgentInstance, InstanceMember
from superaide.vault import crypto, store
from superaide.vault.crypto import VaultError

A = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
B = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
KEY_A = "sk-ant-api03-" + "a" * 40
KEY_B = "sk-ant-api03-" + "b" * 40


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


def save(scope: InstanceScope, provider: str, values: dict[str, str]) -> None:
    with use_instance(scope), session_scope() as s:
        store.save(s, provider, values, user_id=None, meta={})


def runtime(scope: InstanceScope):
    with use_instance(scope):
        return store.runtime_settings()


# --- the vault ---------------------------------------------------------------------------------


def test_each_instance_reads_only_its_own_credentials():
    save(A, "anthropic", {"api_key": KEY_A})
    save(B, "anthropic", {"api_key": KEY_B})  # the same slot in another instance is its own row
    save(A, "telegram", {"bot_token": "123:abc"})
    assert runtime(A).anthropic_api_key == KEY_A and runtime(B).anthropic_api_key == KEY_B
    assert runtime(A).telegram_bot_token == "123:abc" and runtime(B).telegram_bot_token == ""
    with use_instance(B), session_scope() as s:
        assert set(store.stored(s)) == {("anthropic", "api_key")}
        store.remove(s, "anthropic", user_id=None)
    assert runtime(A).anthropic_api_key == KEY_A and runtime(B).anthropic_api_key == ""


def test_a_secret_copied_to_another_instance_does_not_decrypt():
    save(A, "anthropic", {"api_key": KEY_A})
    with session_scope() as s:
        row = s.scalar(select(IntegrationSecret).where(IntegrationSecret.agent_id == A.agent_id))
        assert row is not None and row.aad == "instance" and KEY_A not in row.ciphertext
        row.agent_id, row.tenant_id = B.agent_id, B.tenant_id  # a row moved by hand, or by a bug
    with use_instance(B), session_scope() as s:
        [moved] = store.stored(s).values()
        with pytest.raises(VaultError):
            store.unseal(moved)


def test_a_secret_sealed_before_instances_is_read_then_resealed_to_its_instance():
    with session_scope() as s:
        s.add(
            IntegrationSecret(
                provider="anthropic", name="api_key", ciphertext=crypto.encrypt(KEY_A, "anthropic/api_key"),
                aad="legacy", tenant_id=A.tenant_id, agent_id=A.agent_id,
            )
        )  # fmt: skip
    assert runtime(A).anthropic_api_key == KEY_A
    with session_scope() as s:
        row = s.scalar(select(IntegrationSecret))
        assert row is not None and row.aad == "instance"
        assert crypto.decrypt(row.ciphertext, f"{A.agent_id}/anthropic/api_key") == KEY_A
    assert runtime(A).anthropic_api_key == KEY_A


def test_model_provider_keys_are_per_instance_too():
    from superaide.brain.providers.config import key_for, save_provider

    with use_instance(A), session_scope() as s:
        save_provider(s, "openai", base_url="https://api.openai.com/v1", api_key="sk-a", user_id=None, models=["m"])
    with use_instance(B), session_scope() as s:
        assert key_for(s, "openai") == ""
        save_provider(s, "openai", base_url="https://api.openai.com/v1", api_key="sk-b", user_id=None, models=["m"])
    with use_instance(A), session_scope() as s:
        assert key_for(s, "openai") == "sk-a"


# --- bring your own key --------------------------------------------------------------------------


def test_a_hosted_server_never_lends_its_env_keys_to_an_owner(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-api03-" + "e" * 40)
    monkeypatch.setattr(settings, "telegram_bot_token", "999:env")
    monkeypatch.setattr(settings, "fake_llm", False)
    monkeypatch.setattr(settings, "deploy_mode", "hosted")
    nothing = runtime(B)
    assert (nothing.anthropic_api_key, nothing.telegram_bot_token) == ("", "")
    assert brain_status(nothing).reason == "no_provider"  # "connect your AI model", not someone else's key
    save(A, "anthropic", {"api_key": KEY_A})
    assert brain_status(runtime(A)).ready and runtime(B).anthropic_api_key == ""
    with use_instance(B), session_scope() as s:
        assert store.status(s, runtime(B))["anthropic"]["connected"] is False


def test_a_self_hosted_server_still_uses_its_env_keys(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-api03-" + "e" * 40)
    monkeypatch.setattr(settings, "deploy_mode", "self_hosted")
    assert runtime(B).anthropic_api_key == settings.anthropic_api_key


# --- the kill switch -----------------------------------------------------------------------------


def test_freezing_one_instance_leaves_the_others_running():
    with use_instance(A), session_scope() as s:
        set_frozen(s, True, actor="owner")
    with use_instance(A), session_scope() as s:
        assert is_frozen(s)
    with use_instance(B), session_scope() as s:
        assert not is_frozen(s)


def test_the_platform_switch_freezes_every_instance():
    with session_scope() as s:
        set_platform_frozen(s, True, actor="operator")
    for scope in (A, B):
        with use_instance(scope), session_scope() as s:
            assert is_frozen(s)
    with session_scope() as s:
        set_platform_frozen(s, False, actor="operator")
    with use_instance(A), session_scope() as s:
        assert not is_frozen(s)


# --- the runtime per instance, through the app -----------------------------------------------------


def _owner(app, name: str, email: str) -> TestClient:
    c = TestClient(app, headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": name, "email": email, "password": "correct horse battery"})
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def _scope_of(email: str) -> InstanceScope:
    with session_scope() as s:
        row = s.execute(
            text("SELECT i.tenant_id, i.id FROM agent_instance i JOIN instance_member m ON m.agent_id = i.id "
                 "JOIN user_account u ON u.id = m.user_id WHERE u.email_normalized = :e"),
            {"e": email},
        ).one()  # fmt: skip
        return InstanceScope(tenant_id=row[0], agent_id=row[1])


def test_each_owner_gets_an_agent_built_from_their_own_connections(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_many_owners", True)
    monkeypatch.setattr(settings, "fake_llm", False)
    monkeypatch.setattr(settings, "deploy_mode", "hosted")
    monkeypatch.setattr(settings, "run_jobs_in_api", False)
    monkeypatch.setattr(settings, "run_channels_in_api", False)
    app = create_app()  # a real, per-instance runtime (no fixed test agent)
    a = _owner(app, "Jordan", "s@example.com")
    b = _owner(app, "Priya", "p@example.com")
    save(_scope_of("s@example.com"), "anthropic", {"api_key": KEY_A})
    assert a.get("/app/me").json()["brain"] == "claude"
    assert b.get("/app/me").json()["brain"] == "offline"  # B connected nothing: no borrowing A's key
    with session_scope() as s:
        assert len(s.scalars(select(AgentInstance)).all()) == 2 and len(s.scalars(select(InstanceMember)).all()) == 2


def test_google_sign_in_lands_in_the_instance_that_started_it(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_many_owners", True)

    def google_api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "ya29.x", "refresh_token": "1//refresh-b",
                "scope": "https://www.googleapis.com/auth/calendar.readonly",
            },
        )  # fmt: skip

    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(google_api)))

    app = create_app()
    _owner(app, "Jordan", "s@example.com")
    b = _owner(app, "Priya", "p@example.com")
    sb = _scope_of("p@example.com")
    save(sb, "google", {"client_id": "client-b.apps.googleusercontent.com", "client_secret": "GOCSPX-b"})
    url = b.post("/app/connections/google/start").json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    r = TestClient(app).get(  # the callback comes back with no session at all
        "/app/connections/google/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].endswith("google=connected")
    assert runtime(sb).google_refresh_token == "1//refresh-b"
    assert runtime(_scope_of("s@example.com")).google_refresh_token == ""
