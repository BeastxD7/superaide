"""Ollama silently cuts a prompt to its context window (4096 tokens unless set otherwise).

Ava's prompt (charter, owner brief, memories, tools) is around 8,000 tokens, so on the default window the start
of it is dropped without any error and Ava forgets who she works for. The window cannot be set through
Ollama's OpenAI-compatible endpoint, only in a Modelfile (`PARAMETER num_ctx`). This asks Ollama what the model
runs with (`/api/show`), so the owner can be told, with the exact steps, before it causes amnesia.
Read-only, short timeout, never raises; the answer is cached for a few minutes.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

NEEDED_TOKENS = 12288  # Ava's prompt is about 8k tokens; this leaves room for the conversation and the reply
RECOMMENDED = 16384
OLLAMA_DEFAULT = 4096  # what Ollama runs a model with when no num_ctx is set (OLLAMA_CONTEXT_LENGTH can change it)
CACHE_SECONDS = 300
_NUM_CTX = re.compile(r"^\s*num_ctx\s+(\d+)\s*$", re.MULTILINE)
_cache: dict[tuple[str, str], tuple[float, int | None]] = {}


@dataclass(frozen=True)
class ContextWarning:
    model: str
    tokens: int
    message: str
    steps: str


def root_url(base_url: str) -> str:
    """Ollama's own address from its OpenAI-compatible one (http://localhost:11434/v1 -> http://localhost:11434)."""
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def parse_context(info: dict) -> int:
    """The window the model runs with: a Modelfile `num_ctx` if there is one, else Ollama's default. A model
    whose own maximum is lower than that is limited by its maximum."""
    found = _NUM_CTX.search(str(info.get("parameters") or ""))
    window = int(found.group(1)) if found else OLLAMA_DEFAULT
    for key, value in (info.get("model_info") or {}).items():
        if str(key).endswith(".context_length") and isinstance(value, int) and value > 0:
            window = min(window, value)
    return window


def context_tokens(base_url: str, model: str, *, client: httpx.Client | None = None, fresh: bool = False) -> int | None:
    """The model's window in tokens, or None when Ollama cannot be asked (not Ollama, not running, unknown model)."""
    key = (root_url(base_url), model)
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    tokens: int | None = None
    try:
        http = client or httpx.Client(timeout=3)
        try:
            resp = http.post(f"{key[0]}/api/show", json={"model": model})
        finally:
            if client is None:
                http.close()
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            tokens = parse_context(resp.json())
    except Exception:
        tokens = None
    _cache[key] = (time.monotonic(), tokens)
    return tokens


def modelfile_steps(model: str) -> str:
    """The exact way to give a model a bigger window, ending with choosing the new name in Superaide."""
    new = f"{model.split(':')[0]}-{RECOMMENDED // 1024}k"
    return (
        f"1. Create a file named Modelfile with two lines: FROM {model} and PARAMETER num_ctx {RECOMMENDED}. "
        f"2. Run: ollama create {new} -f Modelfile. "
        f"3. On the AI model page, choose {new} instead of {model}."
    )


def warning(base_url: str, model: str, *, client: httpx.Client | None = None, fresh: bool = False) -> ContextWarning | None:
    tokens = context_tokens(base_url, model, client=client, fresh=fresh)
    if tokens is None or tokens >= NEEDED_TOKENS:
        return None
    message = (
        f"{model} runs on Ollama with a {tokens:,}-token window, but Ava needs about {NEEDED_TOKENS:,}. "
        "Ollama silently cuts the start of what she is told, so she forgets who she works for."
    )
    return ContextWarning(model, tokens, message, modelfile_steps(model))


def forget() -> None:
    _cache.clear()
