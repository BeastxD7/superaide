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
from superaide.brain.models import LlmProvider, ModelRole
from superaide.brain.providers.base import CheckReport
from superaide.brain.wiring import brain_status, build_llm
from superaide.identity.auth.models import IntegrationSecret
from superaide.identity.vault.store import runtime_settings
from superaide.main import create_app
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.platform.security.urls import UnsafeURL, check_base_url
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture)

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
    assert priced.json()["priced"]["openrouter"] == ["acme/fast"]
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


@pytest.mark.parametrize(
    "url",
    ["http://localhost:11434/v1", "http://10.0.0.5:11434/v1", "http://192.168.1.2/v1", "http://[::1]:8000/v1",
     "http://169.254.169.254/latest"],
)  # fmt: skip
def test_hosted_mode_refuses_every_internal_address_even_for_local_servers(url):
    with pytest.raises(UnsafeURL):
        check_base_url(url, allow_private=True, hosted_mode=True)
    assert check_base_url("https://8.8.8.8/v1", allow_private=True, hosted_mode=True) == "https://8.8.8.8/v1"


def test_hosted_mode_refuses_a_local_server_in_the_app_and_never_prices_it_free(owner_client, service, monkeypatch):  # noqa: F811
    from superaide.brain.spend.pricing import PriceRegistry, UnknownModelPrice
    from superaide.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "deploy_mode", "hosted")
    r = owner_client.post("/app/providers/ollama", json={"base_url": "http://localhost:11434/v1"})
    assert r.status_code == 400 and "private network" in r.text and service.seen == []
    with pytest.raises(UnknownModelPrice):
        PriceRegistry(base_urls={"ollama": "http://localhost:11434/v1"}).price_for("llama3.2", "ollama")


BRAVE = "BSA-test-brave-key-0123456789"


def test_web_search_key_is_tested_charged_stored_masked_and_removable(owner_client, monkeypatch):  # noqa: F811
    from superaide.brain.models import SpendEntry
    from superaide.tools import web as webtools

    calls = []

    def fake_search(query, api_key, http=None):
        calls.append(api_key)
        if api_key != BRAVE:
            return "The search service answered with an error (401).", True
        return [webtools.source("https://example.com", "Example", "hi")], False

    monkeypatch.setattr(webtools, "search", fake_search)
    assert owner_client.get("/app/providers").json()["web_search"]["connected"] is False

    bad = owner_client.post("/app/providers/web-search", json={"api_key": "BSA-wrong-key-000000"})
    assert bad.status_code == 400 and "didn't accept" in bad.text

    tested = owner_client.post("/app/providers/web-search/test", json={"api_key": BRAVE})
    assert tested.status_code == 200 and tested.json()["ok"] and BRAVE not in tested.text
    with session_scope() as s:
        assert "brave" in {e.provider for e in s.scalars(select(SpendEntry))}  # the test search was charged
        assert s.scalar(select(IntegrationSecret).where(IntegrationSecret.provider == "brave")) is None  # not saved

    saved = owner_client.post("/app/providers/web-search", json={"api_key": BRAVE})
    body = saved.json()["web_search"]
    assert saved.status_code == 200 and body["connected"] and body["via"] == "app" and body["key_hint"]
    assert BRAVE not in saved.text and runtime_settings().brave_search_api_key == BRAVE

    gone = owner_client.post("/app/providers/web-search/remove")
    assert gone.status_code == 200 and gone.json()["web_search"]["connected"] is False
    assert runtime_settings().brave_search_api_key == ""


def test_openrouter_models_are_priced_from_its_list_without_asking(owner_client, monkeypatch):  # noqa: F811
    listed = {"acme/fast": {"input": "1.000000", "output": "4.000000"}}

    def fake(preset, api_key, base_url):
        class P:
            def check(self):
                return CheckReport(True, "Connected", ["acme/fast", "acme/unpriced"], listed)

        return P()

    monkeypatch.setattr("superaide.app.providers.build_provider", fake)
    r = owner_client.post("/app/providers/openrouter", json={"api_key": KEY})
    assert r.status_code == 200 and r.json()["priced"]["openrouter"] == ["acme/fast"]
    ok = owner_client.put("/app/providers/roles", json={"chat": {"provider": "openrouter", "model": "acme/fast"}})
    assert ok.status_code == 200
    asks = owner_client.put("/app/providers/roles", json={"chat": {"provider": "openrouter", "model": "acme/unpriced"}})
    assert asks.status_code == 409


def test_several_endpoints_of_one_kind_each_have_their_own_key_price_and_role(owner_client, service, monkeypatch):  # noqa: F811
    import ipaddress

    from superaide.platform.security import urls

    # custom = a server on this machine (free); custom-2 = a hosted endpoint (needs a price).
    monkeypatch.setattr(
        urls, "resolve", lambda host: [ipaddress.ip_address("127.0.0.1" if host == "localhost" else "8.8.8.8")]
    )
    assert owner_client.post("/app/providers/custom", json={"base_url": "http://localhost:8000/v1"}).status_code == 200
    second = owner_client.post(
        "/app/providers/custom", json={"key": "custom-2", "api_key": KEY, "base_url": "https://llm.example.com/v1"}
    )
    assert second.status_code == 200
    keys = sorted((p["key"], p["preset"]) for p in second.json()["providers"])
    assert keys == [("custom", "custom"), ("custom-2", "custom")]

    # The same model name is free on the local one and needs a price on the hosted one.
    assert (
        owner_client.put(
            "/app/providers/roles", json={"chat": {"provider": "custom", "model": "acme/fast"}}
        ).status_code
        == 200
    )
    hosted = {"chat": {"provider": "custom-2", "model": "acme/fast"}}
    assert owner_client.put("/app/providers/roles", json=hosted).status_code == 409
    owner_client.post(
        "/app/providers/prices",
        json={"provider": "custom-2", "model": "acme/fast", "input_usd_per_mtok": 2, "output_usd_per_mtok": 8},
    )
    assert owner_client.put("/app/providers/roles", json=hosted).status_code == 200

    settings = runtime_settings()
    assert settings.model_chat == "custom-2::acme/fast" and settings.llm_providers["custom-2"]["api_key"] == KEY
    llm = build_llm(settings.model_copy(update={"fake_llm": False, "anthropic_api_key": ""}))
    assert isinstance(llm, RoutedLLM) and set(llm.routes) == {"custom", "custom-2"}
    prices = llm.guard.prices
    assert prices.price_for("acme/fast", "custom").input == 0 and prices.price_for("acme/fast", "custom-2").input == 2

    # A key that's already another kind is refused; removing one endpoint leaves the other.
    clash = owner_client.post(
        "/app/providers/ollama", json={"key": "custom-2", "base_url": "http://localhost:11434/v1"}
    )
    assert clash.status_code == 409
    left = owner_client.post("/app/providers/custom-2/remove").json()
    assert [p["key"] for p in left["providers"]] == ["custom"] and left["roles"]["chat"]["provider"] == "anthropic"


@pytest.mark.parametrize("name", ["openai", "anthropic", "platform", "ollama", "azure", "openrouter"])
def test_a_custom_endpoint_cannot_take_another_providers_name(owner_client, service, name):  # noqa: F811
    saved = owner_client.post("/app/providers/custom", json={"key": name, "base_url": "http://localhost:8000/v1"})
    assert saved.status_code == 400 and "another provider's name" in saved.json()["detail"]
    tested = owner_client.post(
        "/app/providers/test", json={"preset": "custom", "key": name, "base_url": "http://localhost:8000/v1"}
    )
    assert tested.status_code == 400
    assert service.seen == []  # refused before anything connects
    body = owner_client.get("/app/providers").json()
    assert body["providers"] == [] and body["claude"]["connected"] is False
    with session_scope() as s:
        assert s.scalar(select(LlmProvider)) is None


def test_a_provider_may_still_use_its_own_name_and_a_numbered_one(owner_client, service):  # noqa: F811
    assert owner_client.post("/app/providers/openrouter", json={"api_key": KEY, "key": "openrouter"}).status_code == 200
    numbered = owner_client.post(
        "/app/providers/custom", json={"key": "custom-2", "base_url": "http://localhost:8000/v1"}
    )
    assert numbered.status_code == 200


def test_the_name_clash_message_names_the_provider_even_without_a_key(owner_client, service):  # noqa: F811
    from superaide.brain.providers import config

    with session_scope() as s:
        config.save_provider(s, "openrouter", key="custom", base_url="https://x.example/v1", api_key=KEY,
                             user_id=None, models=[], prices={})  # fmt: skip
    clash = owner_client.post("/app/providers/custom", json={"base_url": "http://localhost:8000/v1"})
    assert clash.status_code == 409 and clash.json()["detail"] == "custom is already used by another provider."


def test_saving_models_for_a_provider_that_is_not_connected_says_which_one_in_plain_words(owner_client, service):  # noqa: F811
    r = owner_client.put("/app/providers/roles", json={"chat": {"provider": "anthropic", "model": "claude-haiku-5-5"}})
    assert r.status_code == 409
    assert r.json()["detail"] == "Connect Claude (Anthropic) first, or choose a different provider for Conversation."
    r = owner_client.put(
        "/app/providers/roles",
        json={"chat": {"provider": "custom-2", "model": "m"}, "deep": {"provider": "openai", "model": "gpt"}},
    )
    assert r.status_code == 409 and "custom-2" in r.json()["detail"] and "Conversation" in r.json()["detail"]


def test_a_role_on_an_unconnected_provider_uses_the_chat_model():
    from superaide.brain.llm import RoutedLLM as Routed

    chat, other = object(), object()
    routed = Routed({"custom": chat, "openai": other}, fallback="custom::tiny")
    assert routed._route("claude-haiku-5-5") == (chat, "tiny")  # Claude isn't connected
    assert routed._route("openai::gpt-5") == (other, "gpt-5")
    with pytest.raises(RuntimeError):
        Routed({"custom": chat})._route("claude-haiku-5-5")
