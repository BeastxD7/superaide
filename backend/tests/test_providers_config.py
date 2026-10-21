"""Choosing Ava's model in the app (ADR 0007 WP5): connect a provider, test it, pick a model per
role, set a price, disconnect. Keys never come back, addresses are checked, and the agent is
built from what the owner chose."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.brain.llm import ClaudeLLM, ProviderLLM, RoutedLLM
from superaide.brain.providers.base import CheckReport
from superaide.db.models import AuditLog, IntegrationSecret, LlmProvider, ModelRole
from superaide.db.session import session_scope
from superaide.factory import brain_status, build_llm
from superaide.main import create_app
from superaide.security.urls import UnsafeURL, check_base_url
from superaide.vault.store import runtime_settings
from tests.test_loop import owner_client  # noqa: F401  (fixture)

KEY = "sk-or-v1-test-secret-key-0123456789"


class FakeService:
    def __init__(self, ok: bool = True, models: list[str] | None = None) -> None:
        self.ok, self.models, self.seen = ok, models or ["acme/fast", "acme/smart"], []

    def __call__(self, preset: str, api_key: str, base_url: str):
        self.seen.append((preset, api_key, base_url))
        service = self

        class P:
            def check(self) -> CheckReport:
                if not service.ok:
                    return CheckReport(False, "OpenRouter didn't accept that key.")
                return CheckReport(True, "Connected", service.models)

        return P()


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr("superaide.app.providers.build_provider", fake)
    return fake


@pytest.mark.parametrize(
    ("url", "private_ok", "error"),
    [
        ("https://8.8.8.8/v1", False, None),
        ("http://8.8.8.8/v1", False, "https"),
        ("http://169.254.169.254/latest", True, "isn't allowed"),
        ("https://user:pw@8.8.8.8/v1", False, "key field"),
        ("https://8.8.8.8/v1?key=1", False, "? or #"),
        ("http://10.0.0.5:11434/v1", False, "private network"),
        ("http://10.0.0.5:11434/v1", True, None),
        ("http://localhost:11434/v1", True, None),
        ("ftp://8.8.8.8", False, "https://"),
    ],
)
def test_addresses_are_checked_before_the_server_connects(url, private_ok, error):
    if error is None:
        assert check_base_url(url, allow_private=private_ok) == url.rstrip("/")
    else:
        with pytest.raises(UnsafeURL, match=re.escape(error)):
            check_base_url(url, allow_private=private_ok)


def test_the_page_lists_every_provider_and_no_keys(owner_client):  # noqa: F811
    body = owner_client.get("/app/providers").json()
    ids = [p["id"] for p in body["presets"]]
    assert ids[0] == "anthropic" and {"openai", "azure", "openrouter", "xai", "gemini", "ollama"} <= set(ids)
    ollama = next(p for p in body["presets"] if p["id"] == "ollama")
    assert ollama["local"] and not ollama["key_required"] and any("Local models" in w for w in ollama["warnings"])
    assert body["roles"]["chat"]["provider"] == "anthropic"


def test_testing_a_connection_saves_nothing_and_never_echoes_the_key(owner_client, service):  # noqa: F811
    r = owner_client.post("/app/providers/test", json={"preset": "openrouter", "api_key": KEY})
    assert r.status_code == 200 and r.json()["models"] == ["acme/fast", "acme/smart"] and KEY not in r.text
    assert service.seen == [("openrouter", KEY, "https://openrouter.ai/api/v1")]
    with session_scope() as s:
        assert s.scalar(select(LlmProvider)) is None
    bad = owner_client.post(
        "/app/providers/test", json={"preset": "custom", "api_key": KEY, "base_url": "http://169.254.169.254/v1"}
    )
    assert bad.status_code == 400 and "isn't allowed" in bad.text and len(service.seen) == 1


def test_connect_choose_price_and_disconnect(owner_client, service):  # noqa: F811
    r = owner_client.post("/app/providers/openrouter", json={"api_key": KEY})
    assert r.status_code == 200 and KEY not in r.text
    [mine] = r.json()["providers"]
    assert mine["preset"] == "openrouter" and mine["key_hint"] and mine["models"] == ["acme/fast", "acme/smart"]
    with session_scope() as s:
        secret = s.scalar(select(IntegrationSecret).where(IntegrationSecret.provider == "llm:openrouter"))
        assert secret is not None and KEY not in secret.ciphertext
        details = [a.detail for a in s.scalars(select(AuditLog).where(AuditLog.actor == "providers"))]
        assert details and all(KEY not in str(d) for d in details)

    # A model with no known price can't be chosen until the owner sets one.
    choice = {"chat": {"provider": "openrouter", "model": "acme/fast"}}
    refused = owner_client.put("/app/providers/roles", json=choice)
    assert refused.status_code == 409 and refused.json()["detail"]["needs_price"] == [choice["chat"]]
    priced = owner_client.post(
        "/app/providers/prices",
        json={"provider": "openrouter", "model": "acme/fast", "input_usd_per_mtok": 1, "output_usd_per_mtok": 4},
    )
    assert priced.status_code == 200
    chosen = owner_client.put("/app/providers/roles", json=choice)
    assert chosen.status_code == 200 and chosen.json()["roles"]["chat"] == choice["chat"]

    settings = runtime_settings()
    assert settings.model_chat == "openrouter::acme/fast" and settings.llm_providers["openrouter"]["api_key"] == KEY
    real = settings.model_copy(update={"fake_llm": False, "anthropic_api_key": ""})
    assert brain_status(real).provider == "openrouter"
    llm = build_llm(real)
    assert isinstance(llm, RoutedLLM) and isinstance(llm.routes["openrouter"], ProviderLLM)
    assert llm.guard is not None and llm.guard.prices.price_for("acme/fast", "openrouter").input == Decimal("1")

    # Keeping the stored key: send no api_key.
    again = owner_client.post("/app/providers/openrouter", json={})
    assert again.status_code == 200 and service.seen[-1][1] == KEY

    gone = owner_client.post("/app/providers/openrouter/remove")
    assert gone.status_code == 200 and gone.json()["providers"] == []
    assert gone.json()["roles"]["chat"]["provider"] == "anthropic"
    with session_scope() as s:
        assert s.scalar(select(ModelRole)) is None
        assert s.scalar(select(IntegrationSecret).where(IntegrationSecret.provider == "llm:openrouter")) is None


def test_a_failed_check_saves_nothing(owner_client, service):  # noqa: F811
    service.ok = False
    r = owner_client.post("/app/providers/openrouter", json={"api_key": KEY})
    assert r.status_code == 400 and "didn't accept" in r.text
    with session_scope() as s:
        assert s.scalar(select(LlmProvider)) is None


def test_a_local_ollama_needs_no_key_and_no_price(owner_client, service):  # noqa: F811
    r = owner_client.post("/app/providers/ollama", json={"base_url": "http://localhost:11434/v1"})
    assert r.status_code == 200
    chosen = owner_client.put("/app/providers/roles", json={"chat": {"provider": "ollama", "model": "llama3.2"}})
    assert chosen.status_code == 200
    settings = runtime_settings().model_copy(update={"fake_llm": False, "anthropic_api_key": ""})
    assert brain_status(settings).ready
    llm = build_llm(settings)
    assert isinstance(llm, RoutedLLM) and llm.guard.prices.price_for("llama3.2", "ollama").input == 0


def test_claude_alone_still_builds_the_plain_claude_client():
    settings = runtime_settings().model_copy(update={"fake_llm": False, "anthropic_api_key": "sk-ant-test"})
    assert isinstance(build_llm(settings), ClaudeLLM)


def test_only_the_owner_can_change_models(agent):
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Not Hired", "email": "n@example.com", "password": "correct horse battery"})
    assert c.get("/app/providers").status_code == 403
    assert c.post("/app/providers/openrouter", json={"api_key": KEY}).status_code == 403
    bare = TestClient(create_app(agent))
    assert bare.post("/app/providers/openrouter", json={"api_key": KEY}).status_code in (401, 403)
