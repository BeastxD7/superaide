# ci-module: brain
"""Ollama silently truncates a prompt longer than its window. The owner is told, with the exact steps, in the
banner and in Check my setup, when the model in use has a window smaller than Ava's prompt."""

from __future__ import annotations

import httpx
import pytest

from superaide.brain import keyhealth
from superaide.brain.providers import config, ollama_ctx
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture)

BASE = "http://localhost:11434/v1"


class FakeShow:
    """A fake Ollama /api/show per model name; counts the calls."""

    def __init__(self, shows: dict[str, dict]) -> None:
        self.shows, self.calls = shows, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        assert request.url.path == "/api/show"
        name = json.loads(request.content)["model"]
        self.calls.append(name)
        if name not in self.shows:
            return httpx.Response(404, json={"error": "model not found"})
        return httpx.Response(200, json=self.shows[name])


DEFAULT = {"parameters": "stop \"<|im_end|>\"\ntemperature 0.6", "model_info": {"qwen3.context_length": 40960}}
BIG = {"parameters": "num_ctx 16384\ntemperature 0.6", "model_info": {"qwen3.context_length": 40960}}


@pytest.fixture
def show(monkeypatch):
    ollama_ctx.forget()
    fake = FakeShow({"qwen3:8b": DEFAULT, "qwen3-16k": BIG})
    real = httpx.Client
    monkeypatch.setattr(ollama_ctx.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(fake)))
    yield fake
    ollama_ctx.forget()


def test_a_model_without_num_ctx_runs_on_ollamas_small_default(show):
    assert ollama_ctx.context_tokens(BASE, "qwen3:8b") == 4096
    assert show.calls == ["qwen3:8b"]


def test_a_modelfile_num_ctx_is_what_counts_and_the_models_own_limit_caps_it(show):
    assert ollama_ctx.context_tokens(BASE, "qwen3-16k") == 16384
    assert ollama_ctx.parse_context({"parameters": "num_ctx 65536", "model_info": {"x.context_length": 8192}}) == 8192


def test_unknown_models_and_a_server_that_is_down_are_not_warnings(show, monkeypatch):
    assert ollama_ctx.context_tokens(BASE, "nope") is None and ollama_ctx.warning(BASE, "nope") is None
    ollama_ctx.forget()

    def boom(**kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(ollama_ctx.httpx, "Client", boom)
    assert ollama_ctx.warning(BASE, "qwen3:8b") is None


def test_the_answer_is_cached_unless_asked_fresh(show):
    ollama_ctx.context_tokens(BASE, "qwen3:8b")
    ollama_ctx.context_tokens(BASE, "qwen3:8b")
    assert show.calls == ["qwen3:8b"]
    ollama_ctx.context_tokens(BASE, "qwen3:8b", fresh=True)
    assert show.calls == ["qwen3:8b"] * 2


def test_the_warning_has_the_exact_modelfile_steps(show):
    found = ollama_ctx.warning(BASE, "qwen3:8b")
    assert found is not None and found.tokens == 4096
    assert "FROM qwen3:8b" in found.steps and "PARAMETER num_ctx 16384" in found.steps
    assert "ollama create qwen3-16k -f Modelfile" in found.steps
    assert ollama_ctx.warning(BASE, "qwen3-16k") is None


def connect(model: str, *, preset: str = "ollama") -> None:
    with session_scope() as s:
        config.save_provider(s, preset, base_url=BASE, api_key="", user_id=None, models=[model])
        config.set_role(s, "chat", preset, model, user_id=None)


def test_a_small_window_raises_the_banner_and_a_warning_in_check_my_setup(owner_client, show):  # noqa: F811
    connect("qwen3:8b")
    me = owner_client.get("/app/me").json()
    assert me["ai_notice"]["reason"] == "context_small"
    assert "qwen3:8b" in me["ai_notice"]["message"] and "PARAMETER num_ctx 16384" in me["ai_notice"]["message"]
    body = owner_client.post("/app/setup-check", headers={"X-Requested-With": "fetch"}).json()
    row = next(x for x in body["results"] if x["name"] == "AI key")
    assert row["status"] == "warn" and "ollama create" in row["fix"] and "4,096" in row["cause"]


def test_a_big_enough_window_or_another_provider_shows_nothing(owner_client, show):  # noqa: F811
    connect("qwen3-16k")
    assert owner_client.get("/app/me").json()["ai_notice"] is None
    connect("qwen3:8b", preset="vllm")
    assert owner_client.get("/app/me").json()["ai_notice"] is None


def test_a_hosted_server_never_contacts_an_owner_typed_address(owner_client, show, monkeypatch):  # noqa: F811
    connect("qwen3:8b")
    monkeypatch.setattr(get_settings(), "deploy_mode", "hosted")
    with session_scope() as s:
        assert keyhealth.small_context(s, get_settings()) is None
    assert show.calls == []
