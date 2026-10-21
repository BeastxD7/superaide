"""Embeddings: turning a fact or a question into a vector, so recall can match meaning, not
just words ("do I eat meat?" finds "Shashank is vegetarian"; ADR 0008 WP2b).

One interface, four providers, chosen by `settings.embedder`:
- `local` (default): fastembed (ONNX on CPU, no torch) with a small multilingual model. Private
  facts never leave the machine and it costs nothing. The model (~220 MB) downloads once into
  `embed_cache_dir`, in the background, and recall skips meaning-search until it's ready.
- `voyage`: Voyage AI's API. Facts leave the machine, so it's the owner's opt-in; every call
  goes on the spend ledger (provider "voyage", purpose "embed") under its own monthly cap and
  the kill switch, and counts towards the overall cap like every other entry.
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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from superaide.config import Settings

log = logging.getLogger(__name__)

Kind = Literal["query", "document"]


class Embedder(Protocol):
    name: str  # stored with every vector: "local:<model>" | "voyage:<model>" | "fake" | "none"
    min_similarity: float  # cosine similarity below which a match means nothing for this model

    def ready(self) -> bool: ...

    def warm(self) -> None: ...

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None: ...


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class NoEmbedder:
    name = "none"
    min_similarity = 1.0

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
    concepts: dict[str, str] = {}  # word → concept, e.g. {"tokyo": "japan"}

    def ready(self) -> bool:
        return True

    def warm(self) -> None:
        return None

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None:
        from superaide.context import lexicon

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

    def __init__(self, model: str, cache_dir: str) -> None:
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
        return [_unit([float(x) for x in v]) for v in self._model.embed(texts)]


VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_PRICE_PER_MTOK = Decimal("0.02")  # voyage-4-lite; docs.voyageai.com/docs/pricing, checked 2026-10-22


class VoyageEmbedder:
    min_similarity = 0.35

    def __init__(self, api_key: str, model: str, monthly_cap_usd: float) -> None:
        self.api_key, self.model = api_key, model
        self.name = f"voyage:{model}"
        self.cap = Decimal(str(monthly_cap_usd))

    def ready(self) -> bool:
        return bool(self.api_key)

    def warm(self) -> None:
        return None

    def _spent_this_month(self, s: Any) -> Decimal:
        from sqlalchemy import func, select

        from superaide.db.models import SpendEntry

        since = datetime.now(UTC) - timedelta(days=30)
        total = s.scalar(
            select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(
                SpendEntry.provider == "voyage", SpendEntry.created_at >= since
            )
        )
        return Decimal(total or 0)

    def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]] | None:
        from superaide.audit import audit
        from superaide.db.models import SpendEntry
        from superaide.db.session import session_scope
        from superaide.policy.killswitch import is_frozen

        if not self.api_key or not texts:
            return None
        worst = Decimal(sum(len(t) for t in texts)) / 3 * VOYAGE_PRICE_PER_MTOK / 1_000_000  # ≥ 1 token / 3 chars
        with session_scope() as s:
            if is_frozen(s) or self._spent_this_month(s) + worst > self.cap:
                audit(s, "budget", "embed_refused", provider="voyage", cap_usd=float(self.cap))
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
        with session_scope() as s:
            cost = Decimal(tokens) * VOYAGE_PRICE_PER_MTOK / 1_000_000
            s.add(
                SpendEntry(
                    provider="voyage", purpose="embed", model=self.model, status="settled", reserved_usd=cost,
                    cost_usd=cost, input_tokens=tokens, settled_at=datetime.now(UTC),
                )
            )  # fmt: skip
        rows = sorted(body.get("data") or [], key=lambda d: d.get("index", 0))
        return [_unit([float(x) for x in d["embedding"]]) for d in rows]


_cache: dict[tuple[str, ...], Embedder] = {}


def get_embedder(settings: Settings) -> Embedder:
    """The configured embedder, one per process (the local model loads once)."""
    choice = (settings.embedder or "none").lower()
    key: tuple[str, ...]
    if choice == "local":
        key = ("local", settings.embed_model_local, settings.embed_cache_dir)
    elif choice == "voyage" and settings.voyage_api_key:
        key = ("voyage", settings.embed_model_voyage, settings.voyage_api_key, str(settings.embed_monthly_usd))
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
        elif key[0] == "fake":
            _cache[key] = FakeEmbedder()
        else:
            _cache[key] = NoEmbedder()
    return _cache[key]
