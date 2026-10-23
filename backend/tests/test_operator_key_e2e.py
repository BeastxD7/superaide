"""The operator's own Ava gets the server key through the real request path (no fixed test agent)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from superaide.config import get_settings
from superaide.main import create_app

SERVER_KEY = "sk-ant-server-env-key-111111111111111"


@pytest.fixture
def hosted_operator(monkeypatch):
    env = get_settings()
    monkeypatch.setattr(env, "deploy_mode", "hosted")
    monkeypatch.setattr(env, "anthropic_api_key", SERVER_KEY)
    monkeypatch.setattr(env, "platform_anthropic_api_key", SERVER_KEY)
    monkeypatch.setattr(env, "platform_key_owner_emails", "jordan@example.com")
    monkeypatch.setattr(env, "fake_llm", False)
    monkeypatch.setattr(env, "run_channels_in_api", False)
    monkeypatch.setattr(env, "run_jobs_in_api", False)
    return env


def test_the_operators_web_chat_is_not_offline(hosted_operator, monkeypatch):
    built = []
    import superaide.factory as factory

    real = factory.build_llm

    def spy(settings):
        built.append(settings.anthropic_api_key == SERVER_KEY)
        return real(settings)

    monkeypatch.setattr(factory, "build_llm", spy)
    c = TestClient(create_app(), headers={"X-Requested-With": "superaide"})
    assert c.post("/auth/signup", json={"name": "Jordan", "email": "jordan@example.com",
                                        "password": "correct horse battery"}).status_code == 200  # fmt: skip
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    me = c.get("/app/me").json()
    assert me["brain"] == "claude", me
    assert built and built[-1] is True


def test_the_operators_chat_stream_answers_with_the_server_key(hosted_operator, monkeypatch):
    from superaide.brain.llm import ClaudeLLM, StepResult
    from superaide.brain.pricing import UsageCounts

    keys = []

    def step(self, **kwargs):
        keys.append(self.provider.client.api_key if hasattr(self.provider.client, "api_key") else None)
        return StepResult([{"type": "text", "text": "Hello from Claude."}], "end_turn", UsageCounts())

    monkeypatch.setattr(ClaudeLLM, "step", step)
    c = TestClient(create_app(), headers={"X-Requested-With": "superaide"})
    c.post(
        "/auth/signup",
        json={"name": "Jordan", "email": "Jordan@Example.com", "password": "correct horse battery"},
    )
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    with c.stream("POST", "/app/chat/stream", json={"text": "hi"}) as r:
        body = "".join(r.iter_text())
    assert "Hello from Claude." in body and "Offline mode" not in body
    assert keys and keys[-1] == SERVER_KEY
    assert SERVER_KEY not in body


def test_someone_else_on_the_same_server_stays_offline(hosted_operator, monkeypatch):
    monkeypatch.setattr(hosted_operator, "allow_many_owners", True)
    c = TestClient(create_app(), headers={"X-Requested-With": "superaide"})
    c.post(
        "/auth/signup",
        json={"name": "Jordan", "email": "jordan@example.com", "password": "correct horse battery"},
    )
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    other = TestClient(create_app(), headers={"X-Requested-With": "superaide"})
    other.post(
        "/auth/signup", json={"name": "Bea Lim", "email": "bea@example.com", "password": "correct horse battery"}
    )
    assert other.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    assert other.get("/app/me").json()["brain"] == "offline"
    assert c.get("/app/me").json()["brain"] == "claude"
