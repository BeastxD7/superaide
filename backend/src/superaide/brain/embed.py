"""Embeddings: turning a fact or a question into a vector, so recall can match meaning, not
just words ("do I eat meat?" finds "Jordan is vegetarian"; ADR 0008 WP2b).

One interface, six providers, chosen by `settings.embedder`:
- `local` (default): fastembed (ONNX on CPU, no torch) with a small multilingual model. Private
  facts never leave the machine and it costs nothing. The model (~220 MB) downloads once into
  `embed_cache_dir`, in the background, and recall skips meaning-search until it's ready.
- `voyage`: Voyage AI's API. Facts leave the machine, so it's the owner's opt-in; every call
  goes on the spend ledger (provider "voyage", purpose "embed") under its own monthly cap and
  the kill switch, and counts towards the overall cap like every other entry.
- `ollama`: Ollama's `/api/embed` on this computer (loopback only, free). A model that wants a query or a
  document prefix gets it (`KNOWN_MODELS`); an Ollama on another host is refused, since that sends facts off the
  machine (an owner decision, #647).
- `openai`: any OpenAI-compatible `/embeddings` endpoint. On this computer (LM Studio, llama.cpp) it is free; on
  another host it needs a key and a price per million tokens, goes on the spend ledger under the same monthly cap and
  the kill switch as Voyage, and is refused without a price.
- `fake`: a deterministic bag-of-words vector for tests.
- `none`: no embeddings; recall stays keyword-only.

Embedding never runs in the chat's request path except for one short query, and only when
the provider is ready; facts are embedded by background jobs (context/vectors.py).
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

from superaide.platform.config import Settings

log = logging.getLogger(__name__)

Kind = Literal["query", "document"]


class Embedder(Protocol):
    name: str  # stored with every vector: "local:<model>" | "voyage:<model>" | "ollama:<model>" | "fake" | "none"
    min_similarity: float  # cosine similarity below which a match means nothing for this model
    dimension: int | None  # the vector length; None until a reply shows it
    query_prefix: str  # put before a question, for models trained with one ("search_query: ")
    document_prefix: str  # put before a passage being indexed ("search_document: ")

    def ready(self) -> bool: ...

    def warm(self) -> None: ...

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None: ...


@dataclass(frozen=True)
class ModelSpec:
    dimension: int
    query_prefix: str = ""
    document_prefix: str = ""
    min_similarity: float = 0.3  # not measured per model yet: the golden set decides (#647 item 5)


# What the model cards say each embedding model needs (report 6.4). Keyed by the model's name without its tag.
KNOWN_MODELS: dict[str, ModelSpec] = {
    "nomic-embed-text": ModelSpec(768, "search_query: ", "search_document: "),
    "embeddinggemma": ModelSpec(768, "task: search result | query: ", "title: none | text: "),
    "qwen3-embedding": ModelSpec(
        1024, "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
    ),
    "bge-m3": ModelSpec(1024),
    "snowflake-arctic-embed2": ModelSpec(1024, "query: "),
    "all-minilm": ModelSpec(384),
}


def spec_for(model: str) -> ModelSpec | None:
    name = model.split(":", 1)[0].rsplit("/", 1)[-1].lower()  # "nomic-ai/nomic-embed-text-v1.5:latest" -> the model
    return next((spec for key, spec in KNOWN_MODELS.items() if name.startswith(key)), None)


def with_prefix(texts: list[str], kind: Kind, query_prefix: str, document_prefix: str) -> list[str]:
    """The texts as the model was trained to see them: the query prefix for questions, the document one for passages."""
    prefix = query_prefix if kind == "query" else document_prefix
    return [prefix + t for t in texts] if prefix else list(texts)


def is_loopback(url: str) -> bool:
    """Whether a URL points at this computer (the only place facts may go without the owner's decision)."""
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class NoEmbedder:
    name = "none"
    min_similarity = 1.0
    dimension: int | None = None
    query_prefix = document_prefix = ""

    def ready(self) -> bool:
        return False

    def warm(self) -> None:
        return None

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None:
        return None


class FakeEmbedder:
    """Words (with their spellings and synonyms, plus any `concepts` a test names) hashed into
    64 buckets. Deterministic and free; enough to test the plumbing and the gate."""

    name = "fake"
    min_similarity = 0.3
    dimension: int | None = 64
    query_prefix = document_prefix = ""
    concepts: dict[str, str] = {}  # word → concept, e.g. {"tokyo": "japan"}

    def ready(self) -> bool:
        return True

    def warm(self) -> None:
        return None

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None:
        from superaide.memory.recall import lexicon

        out = []
        for t in texts:
            v = [0.0] * 64
            for w in lexicon.words(t):
                for form in {self.concepts[w]} if w in self.concepts else lexicon.spellings(w):
                    v[int(hashlib.sha256(form.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(_unit(v) if any(v) else v)
        return out


class LocalEmbedder:
    """fastembed on CPU. Loading (and the first download) happens on a background thread."""

    min_similarity = 0.3  # paraphrase-multilingual-MiniLM: real matches score 0.3–0.6
    dimension: int | None = None

    def __init__(self, model: str, cache_dir: str) -> None:
        spec = spec_for(model)
        self.query_prefix = spec.query_prefix if spec else ""  # MiniLM has none; a prefixed model gets its own
        self.document_prefix = spec.document_prefix if spec else ""
        self.model_name = model
        self.name = f"local:{model}"
        self.cache_dir = cache_dir or str(Path.home() / ".cache" / "superaide" / "models")
        self._model: Any = None
        self._lock = threading.Lock()
        self._loading = False
        self._failed = False

    def ready(self) -> bool:
        return self._model is not None

    @property
    def failed(self) -> bool:
        return self._failed

    def warm(self) -> None:
        """Start loading in the background (returns at once)."""
        with self._lock:
            if self._model is not None or self._loading or self._failed:
                return
            self._loading = True
        threading.Thread(target=self._load, name="embed-load", daemon=True).start()

    def _load(self) -> None:
        try:
            from fastembed import TextEmbedding

            Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
            model = TextEmbedding(self.model_name, cache_dir=self.cache_dir)
            list(model.embed(["warm up"]))
            self._model = model
            log.info("local embedder ready: %s", self.model_name)
        except Exception:
            self._failed = True  # recall stays keyword-only; logged once
            log.exception("local embedder couldn't load %s", self.model_name)
        finally:
            self._loading = False

    def load_now(self) -> bool:
        """Load in this thread (background jobs may wait; the chat never does)."""
        if self._model is None and not self._failed:
            with self._lock:
                if self._model is None:
                    self._loading = True
                    self._load()
        return self._model is not None

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None:
        if self._model is None:
            return None
        out = [
            _unit([float(x) for x in v])
            for v in self._model.embed(with_prefix(texts, kind, self.query_prefix, self.document_prefix))
        ]
        self.dimension = len(out[0]) if out else self.dimension
        return out


VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_PRICE_PER_MTOK = Decimal("0.02")  # voyage-4-lite; docs.voyageai.com/docs/pricing, checked 2026-10-22


def spent_this_month(s: Any) -> Decimal:
    """What every paid embedder together spent in the last 30 days (one cap, `embed_monthly_usd`)."""
    from sqlalchemy import func, select

    from superaide.brain.models import SpendEntry

    since = datetime.now(UTC) - timedelta(days=30)
    total = s.scalar(
        select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(
            SpendEntry.purpose == "embed", SpendEntry.created_at >= since
        )
    )
    return Decimal(total or 0)


def reserve_embed_spend(provider: str, cap: Decimal, worst: Decimal) -> bool:
    """False (and an audit line) when the kill switch is on or `worst` would pass the monthly cap."""
    from superaide.platform.audit import audit
    from superaide.platform.db.session import session_scope
    from superaide.policy.killswitch import is_frozen

    with session_scope() as s:
        if is_frozen(s) or spent_this_month(s) + worst > cap:
            audit(s, "budget", "embed_refused", provider=provider, cap_usd=float(cap))
            return False
    return True


def settle_embed_spend(provider: str, model: str, tokens: int, per_mtok: Decimal) -> None:
    from superaide.brain.models import SpendEntry
    from superaide.platform.db.session import session_scope

    with session_scope() as s:
        cost = Decimal(tokens) * per_mtok / 1_000_000
        s.add(
            SpendEntry(
                provider=provider, purpose="embed", model=model, status="settled", reserved_usd=cost,
                cost_usd=cost, input_tokens=tokens, settled_at=datetime.now(UTC),
            )
        )  # fmt: skip


class VoyageEmbedder:
    min_similarity = 0.35
    dimension: int | None = 1024
    query_prefix = document_prefix = ""  # Voyage takes `input_type` instead

    def __init__(self, api_key: str, model: str, monthly_cap_usd: float) -> None:
        self.api_key, self.model = api_key, model
        self.name = f"voyage:{model}"
        self.cap = Decimal(str(monthly_cap_usd))

    def ready(self) -> bool:
        return bool(self.api_key)

    def warm(self) -> None:
        return None

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None:
        if not self.api_key or not texts:
            return None
        worst = Decimal(sum(len(t) for t in texts)) / 3 * VOYAGE_PRICE_PER_MTOK / 1_000_000  # ≥ 1 token / 3 chars
        if not reserve_embed_spend("voyage", self.cap, worst):
            return None
        try:
            r = httpx.post(
                VOYAGE_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": self.model, "input_type": kind, "output_dimension": 1024},
                timeout=20,
            )
            r.raise_for_status()
            body = r.json()
        except Exception as exc:
            log.warning("voyage embed failed: %s", exc)
            return None
        tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
        settle_embed_spend("voyage", self.model, tokens, VOYAGE_PRICE_PER_MTOK)
        rows = sorted(body.get("data") or [], key=lambda d: d.get("index", 0))
        return [_unit([float(x) for x in d["embedding"]]) for d in rows]


class OllamaEmbedder:
    """Ollama's `/api/embed`, on this computer. Free, so nothing goes on the ledger; a host that is not loopback is
    refused (the facts would leave the machine, which is the owner's decision, not a setting's)."""

    remote = False

    def __init__(self, base_url: str, model: str, query_prefix: str = "", document_prefix: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = f"ollama:{model}"
        spec = spec_for(model)
        self.dimension = spec.dimension if spec else None
        self.min_similarity = spec.min_similarity if spec else 0.3
        self.query_prefix = query_prefix or (spec.query_prefix if spec else "")
        self.document_prefix = document_prefix or (spec.document_prefix if spec else "")
        self._refused = not is_loopback(self.base_url)
        if self._refused:
            log.warning("ollama embedder refused: %s is not on this computer", urlparse(self.base_url).hostname)

    def ready(self) -> bool:
        return not self._refused

    def warm(self) -> None:
        return None

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None:
        if self._refused or not texts:
            return None
        try:
            r = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": with_prefix(texts, kind, self.query_prefix, self.document_prefix)},
                timeout=60,
            )
            r.raise_for_status()
            rows = r.json().get("embeddings") or []
        except Exception as exc:
            log.warning("ollama embed failed: %s", exc)
            return None
        if len(rows) != len(texts):
            return None
        out = [_unit([float(x) for x in v]) for v in rows]
        self.dimension = len(out[0])
        return out


class OpenAICompatEmbedder:
    """Any OpenAI-compatible `/embeddings` endpoint (`base_url` ends in `/v1`). On this computer it is free. Anywhere
    else it needs a key and a price per million tokens, runs under the monthly cap and the kill switch, and goes on
    the ledger; with no price it is refused (an unpriced call could not be capped)."""

    def __init__(
        self, base_url: str, api_key: str, model: str, *, price_per_mtok: float, monthly_cap_usd: float,
        dimensions: int = 0, query_prefix: str = "", document_prefix: str = "",
    ) -> None:  # fmt: skip
        self.base_url = base_url.rstrip("/")
        self.api_key, self.model = api_key, model
        self.name = f"openai:{model}"
        self.price = Decimal(str(price_per_mtok))
        self.cap = Decimal(str(monthly_cap_usd))
        self.requested_dimensions = dimensions
        spec = spec_for(model)
        self.dimension = dimensions or (spec.dimension if spec else None)
        self.min_similarity = spec.min_similarity if spec else 0.3
        self.query_prefix = query_prefix or (spec.query_prefix if spec else "")
        self.document_prefix = document_prefix or (spec.document_prefix if spec else "")
        self.local = is_loopback(self.base_url)
        self.remote = not self.local

    def ready(self) -> bool:
        if not self.base_url:
            return False
        return self.local or bool(self.api_key and self.price > 0)

    def warm(self) -> None:
        return None

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None:
        if not self.ready() or not texts:
            return None
        sent = with_prefix(texts, kind, self.query_prefix, self.document_prefix)
        if not self.local:
            worst = Decimal(sum(len(t) for t in sent)) / 3 * self.price / 1_000_000  # ≥ 1 token / 3 chars
            if not reserve_embed_spend("openai-compat", self.cap, worst):
                return None
        body: dict[str, Any] = {"input": sent, "model": self.model}
        if self.requested_dimensions:
            body["dimensions"] = self.requested_dimensions
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = httpx.post(f"{self.base_url}/embeddings", headers=headers, json=body, timeout=60)
            r.raise_for_status()
            reply = r.json()
        except Exception as exc:
            log.warning("openai-compatible embed failed: %s", exc)
            return None
        if not self.local:
            tokens = int((reply.get("usage") or {}).get("total_tokens") or 0)
            settle_embed_spend("openai-compat", self.model, tokens, self.price)
        rows = sorted(reply.get("data") or [], key=lambda d: d.get("index", 0))
        if len(rows) != len(texts):
            return None
        out = [_unit([float(x) for x in d["embedding"]]) for d in rows]
        self.dimension = len(out[0])
        return out


_cache: dict[tuple[str, ...], Embedder] = {}


def get_embedder(settings: Settings) -> Embedder:
    """The configured embedder, one per process (the local model loads once)."""
    choice = (settings.embedder or "none").lower()
    key: tuple[str, ...]
    if choice == "local":
        key = ("local", settings.embed_model_local, settings.embed_cache_dir)
    elif choice == "voyage" and settings.voyage_api_key:
        key = ("voyage", settings.embed_model_voyage, settings.voyage_api_key, str(settings.embed_monthly_usd))
    elif choice == "ollama":
        key = ("ollama", settings.embed_ollama_url, settings.embed_model_ollama, settings.embed_query_prefix,
               settings.embed_document_prefix)  # fmt: skip
    elif choice == "openai" and settings.embed_openai_url:
        key = ("openai", settings.embed_openai_url, settings.embed_openai_key, settings.embed_model_openai,
               str(settings.embed_openai_price_per_mtok_usd), str(settings.embed_openai_dimensions),
               str(settings.embed_monthly_usd), settings.embed_query_prefix,
               settings.embed_document_prefix)  # fmt: skip
    elif choice == "fake":
        key = ("fake",)
    else:
        key = ("none",)
    if key not in _cache:
        if key[0] == "local":
            _cache[key] = LocalEmbedder(settings.embed_model_local, settings.embed_cache_dir)
        elif key[0] == "voyage":
            _cache[key] = VoyageEmbedder(
                settings.voyage_api_key, settings.embed_model_voyage, settings.embed_monthly_usd
            )
        elif key[0] == "ollama":
            _cache[key] = OllamaEmbedder(
                settings.embed_ollama_url, settings.embed_model_ollama, settings.embed_query_prefix,
                settings.embed_document_prefix,
            )  # fmt: skip
        elif key[0] == "openai":
            _cache[key] = OpenAICompatEmbedder(
                settings.embed_openai_url, settings.embed_openai_key, settings.embed_model_openai,
                price_per_mtok=settings.embed_openai_price_per_mtok_usd, monthly_cap_usd=settings.embed_monthly_usd,
                dimensions=settings.embed_openai_dimensions, query_prefix=settings.embed_query_prefix,
                document_prefix=settings.embed_document_prefix,
            )  # fmt: skip
        elif key[0] == "fake":
            _cache[key] = FakeEmbedder()
        else:
            _cache[key] = NoEmbedder()
    return _cache[key]
