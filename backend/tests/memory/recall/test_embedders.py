"""#647: the Ollama and OpenAI-compatible embedders: what they send (prefixes, dimensions), where they may send it, what
they cost and where they stop. Every request is captured by a stand-in for `httpx.post`; nothing reaches a network."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from superaide.brain import embed
from superaide.brain.embed import (
    FakeEmbedder,
    LocalEmbedder,
    NoEmbedder,
    OllamaEmbedder,
    OpenAICompatEmbedder,
    VoyageEmbedder,
    get_embedder,
    is_loopback,
    spec_for,
    with_prefix,
)
from superaide.brain.models import SpendEntry
from superaide.platform.config import Settings
from superaide.platform.db.session import session_scope
from superaide.policy.killswitch import set_frozen


class Resp:
    def __init__(self, body: dict) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.body


@pytest.fixture
def wire(monkeypatch):
    """Captures every request; answers with one 2-dimensional vector per input, in both providers' shapes."""
    sent: list[dict] = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append({"url": url, "json": json, "headers": headers or {}})
        n = len(json["input"])
        if url.endswith("/api/embed"):
            return Resp({"embeddings": [[3.0, 4.0]] * n})
        return Resp(
            {"data": [{"index": i, "embedding": [3.0, 4.0]} for i in range(n)], "usage": {"total_tokens": 1000}}
        )

    monkeypatch.setattr(embed.httpx, "post", post)
    return sent


def _spent(provider: str) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(SpendEntry).where(SpendEntry.provider == provider)) or 0


# --- prefixes ---------------------------------------------------------------------------------------


def test_an_ollama_model_with_prefixes_gets_the_query_prefix_for_questions_and_the_document_prefix_for_passages(wire):
    e = OllamaEmbedder("http://localhost:11434", "nomic-embed-text:v1.5")
    assert e.embed(["bill payment pending"], kind="query") == [[0.6, 0.8]]  # unit length
    assert e.embed(["Invoice 4471 is overdue."], kind="document") is not None
    assert wire[0]["url"] == "http://localhost:11434/api/embed"
    assert wire[0]["json"] == {"model": "nomic-embed-text:v1.5", "input": ["search_query: bill payment pending"]}
    assert wire[1]["json"]["input"] == ["search_document: Invoice 4471 is overdue."]
    assert e.name == "ollama:nomic-embed-text:v1.5" and e.dimension == 2  # learned from the reply


def test_a_model_without_prefixes_is_sent_the_text_unchanged(wire):
    OllamaEmbedder("http://127.0.0.1:11434", "bge-m3").embed(["hello"], kind="query")
    assert wire[0]["json"]["input"] == ["hello"]
    assert spec_for("some-new-model") is None
    assert with_prefix(["a"], "query", "", "") == ["a"]


def test_the_owner_can_override_a_prefix(wire):
    OllamaEmbedder("http://localhost:11434", "nomic-embed-text", "q: ", "d: ").embed(["x"], kind="query")
    assert wire[0]["json"]["input"] == ["q: x"]


def test_each_known_model_declares_its_dimension_prefixes_and_floor():
    nomic = spec_for("nomic-embed-text")
    assert (nomic.dimension, nomic.query_prefix, nomic.document_prefix) == (768, "search_query: ", "search_document: ")
    assert spec_for("embeddinggemma:300m").dimension == 768 and spec_for("embeddinggemma").query_prefix
    assert spec_for("qwen3-embedding:0.6b").dimension == 1024
    assert spec_for("snowflake-arctic-embed2").query_prefix == "query: "
    assert 0 < OllamaEmbedder("http://localhost:11434", "bge-m3").min_similarity < 1


def test_the_openai_compatible_embedder_sends_prefixes_and_the_requested_dimensions(wire):
    e = OpenAICompatEmbedder(
        "http://localhost:1234/v1", "", "nomic-embed-text", price_per_mtok=0, monthly_cap_usd=1, dimensions=256
    )
    assert e.embed(["bill payment pending"], kind="query") == [[0.6, 0.8]]
    e.embed(["Invoice 4471 is overdue."], kind="document")
    assert wire[0]["url"] == "http://localhost:1234/v1/embeddings"
    assert wire[0]["json"] == {
        "input": ["search_query: bill payment pending"],
        "model": "nomic-embed-text",
        "dimensions": 256,
    }
    assert wire[1]["json"]["input"] == ["search_document: Invoice 4471 is overdue."]
    assert e.dimension == 2 and "Authorization" not in wire[0]["headers"]


def test_the_local_fastembed_embedder_applies_a_models_prefixes_too():
    seen: list[list[str]] = []

    class Model:
        def embed(self, texts):
            seen.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

    local = LocalEmbedder("nomic-ai/nomic-embed-text-v1.5", "")
    local._model = Model()
    local.embed(["q"], kind="query")
    local.embed(["d"], kind="document")
    assert seen == [["search_query: q"], ["search_document: d"]]
    plain = LocalEmbedder("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "")  # the default: none
    plain._model = Model()
    plain.embed(["q"], kind="query")
    assert seen[-1] == ["q"] and plain.name == "local:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


# --- where facts may go -----------------------------------------------------------------------------


def test_ollama_on_another_host_is_refused_because_the_facts_would_leave_the_machine(wire):
    e = OllamaEmbedder("http://gpu-box.example.com:11434", "nomic-embed-text")
    assert not e.ready() and e.embed(["a private fact"], kind="document") is None
    assert wire == []
    assert is_loopback("http://localhost:11434") and is_loopback("http://127.0.0.1:1") and is_loopback("http://[::1]:9")
    assert not is_loopback("http://localhost.evil.example.com") and not is_loopback("https://api.openai.com/v1")


def test_a_remote_openai_compatible_endpoint_with_no_price_or_no_key_is_refused(wire):
    for key, price in (("", 0.02), ("k", 0), ("k", 0.0)):
        e = OpenAICompatEmbedder(
            "https://api.example.com/v1", key, "text-embedding-3-small", price_per_mtok=price, monthly_cap_usd=1
        )
        assert not e.ready() and e.embed(["a private fact"], kind="document") is None
    assert wire == [] and _spent("openai-compat") == 0


def test_a_local_openai_compatible_server_costs_nothing_and_needs_no_key(wire):
    e = OpenAICompatEmbedder("http://localhost:1234/v1", "", "all-minilm", price_per_mtok=0, monthly_cap_usd=1)
    assert e.ready() and e.embed(["a"], kind="document") is not None
    assert _spent("openai-compat") == 0


# --- the spend cap ----------------------------------------------------------------------------------


def test_a_remote_embedder_is_on_the_ledger_and_stops_at_the_monthly_cap(wire):
    e = OpenAICompatEmbedder(
        "https://api.example.com/v1", "k", "text-embedding-3-small", price_per_mtok=0.02, monthly_cap_usd=0.00002
    )
    assert e.embed(["Jordan is vegetarian."], kind="document") is not None
    assert wire[0]["headers"] == {"Authorization": "Bearer k"}
    with session_scope() as s:
        entry = s.scalar(select(SpendEntry).where(SpendEntry.provider == "openai-compat"))
        assert entry.purpose == "embed" and entry.cost_usd == Decimal("0.00002")
    assert e.embed(["Jordan is vegetarian."], kind="document") is None  # the next would pass the cap
    assert len(wire) == 1


def test_the_cap_is_shared_by_every_paid_embedder(wire):
    voyage = VoyageEmbedder("k", "voyage-4-lite", monthly_cap_usd=0.00002)
    assert voyage.embed(["a"], kind="document") is not None  # 1000 tokens: the whole month's cap
    remote = OpenAICompatEmbedder("https://api.example.com/v1", "k", "m", price_per_mtok=0.02, monthly_cap_usd=0.00002)
    assert remote.embed(["b"], kind="document") is None
    assert len(wire) == 1


def test_the_kill_switch_stops_a_remote_embedder_but_not_a_local_one(wire):
    with session_scope() as s:
        set_frozen(s, True, actor="test")
    remote = OpenAICompatEmbedder("https://api.example.com/v1", "k", "m", price_per_mtok=0.02, monthly_cap_usd=1)
    assert remote.embed(["a"], kind="document") is None
    local = OllamaEmbedder("http://localhost:11434", "bge-m3")  # free and on this computer: nothing to cap
    assert local.embed(["a"], kind="document") is not None


# --- choosing one -----------------------------------------------------------------------------------


def test_the_settings_choose_the_embedder():
    assert isinstance(get_embedder(Settings(embedder="ollama")), OllamaEmbedder)
    assert get_embedder(Settings(embedder="ollama", embed_model_ollama="bge-m3")).name == "ollama:bge-m3"
    assert isinstance(get_embedder(Settings(embedder="openai", embed_openai_url="")), NoEmbedder)  # no endpoint: none
    e = get_embedder(Settings(embedder="openai", embed_openai_url="http://localhost:1234/v1", embed_model_openai="m"))
    assert isinstance(e, OpenAICompatEmbedder) and e.name == "openai:m" and e.ready()
    assert isinstance(get_embedder(Settings(embedder="local")), LocalEmbedder)
    assert isinstance(get_embedder(Settings(embedder="fake")), FakeEmbedder)
    assert get_embedder(Settings(embedder="local")).name.startswith("local:")  # stored names are unchanged
