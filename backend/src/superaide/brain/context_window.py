"""How much each model can read, how much of that Ava may fill, and whether a server cut the prompt (#641).

Pure functions, no database and no model. The window of a model comes from, in this order: the owner's override,
a probe of the local server (Ollama: `ollama_ctx`, which asks what the model really runs with), a table of
hosted models, then a deliberately small default so an unknown model is never assumed to be roomy. The input budget
is what a prompt may use: window, minus the room kept for the answer, minus 10% because every token count here is
an estimate. Everything here is decided in code; the model is never asked how big its window is.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

SAFETY = 0.10  # of the window, kept free because token counts are estimates
CHARS_PER_TOKEN = 3.5  # conservative: English prompt text and JSON schemas measure 4.3 to 4.5 on qwen3, code about 3
DEFAULT_HOSTED = 32_768  # a hosted model the table does not know: assume a modest window, never a large one
DEFAULT_LOCAL = 4_096  # a local model whose server did not say: Ollama's historical default
TRUNCATION_RATIO = 1.6  # reported tokens this many times below the estimate: the server cut the prompt
TRUNCATION_MIN_GAP = 256  # and by at least this many tokens, so a short prompt's rounding never counts

# First match wins, so the specific names come before the family names. Lower-case substrings of the model id.
KNOWN_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude", 200_000),
    ("gpt-4.1", 1_047_576),
    ("gpt-5", 400_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("gpt-4", 8_192),
    ("gpt-3.5", 16_385),
    ("o1", 200_000),
    ("o3", 200_000),
    ("o4", 200_000),
    ("gemini", 1_048_576),
    ("grok", 131_072),
)


@dataclass(frozen=True)
class WindowInfo:
    tokens: int
    source: str  # override | probe | table | default


def table_window(model: str) -> int | None:
    name = model.lower()
    for part, window in KNOWN_WINDOWS:
        if part in name:
            return window
    return None


def resolve_window(
    model: str, *, provider: str = "", base_url: str = "", override: int | None = None, probe: bool = True
) -> WindowInfo:
    """The window a request to `model` runs with. `provider` is the preset id ("ollama", "openai", "anthropic"...).
    A local server that does not say is assumed small (DEFAULT_LOCAL); an unknown hosted model, DEFAULT_HOSTED."""
    if override and override > 0:
        return WindowInfo(int(override), "override")
    if provider == "ollama" and probe:
        from superaide.brain.providers import ollama_ctx

        found = ollama_ctx.context_tokens(base_url, model)
        if found:
            return WindowInfo(found, "probe")
    local = provider in ("ollama", "vllm")
    if not local and (known := table_window(model)) is not None:
        return WindowInfo(known, "table")
    return WindowInfo(DEFAULT_LOCAL if local else DEFAULT_HOSTED, "default")


def input_budget(window: int, output_reserve: int) -> int:
    """What a prompt may use: window - room for the answer - 10% of the window. Never negative."""
    return max(0, window - max(0, output_reserve) - math.ceil(window * SAFETY))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def _chars(obj: Any) -> int:
    return len(json.dumps(obj, default=str, ensure_ascii=False))


def estimate_request(system: Any, messages: Any, tools: Any) -> int:
    """Tokens the prompt will take: system blocks (objects with `.text`, or strings), messages and tool schemas."""
    blocks = system if isinstance(system, list) else [system]
    chars = sum(len(getattr(b, "text", b) or "") for b in blocks)
    chars += _chars(messages) + (_chars(tools) if tools else 0)
    return math.ceil(chars / CHARS_PER_TOKEN)


def truncated(sent_estimate: int, reported: int) -> bool:
    """The provider counted far fewer prompt tokens than were sent: it cut the start of the prompt (Ollama does
    this silently at its window). `reported` includes cached tokens. 0 means the server did not say."""
    if reported <= 0:
        return False
    return sent_estimate - reported >= TRUNCATION_MIN_GAP and sent_estimate >= reported * TRUNCATION_RATIO
