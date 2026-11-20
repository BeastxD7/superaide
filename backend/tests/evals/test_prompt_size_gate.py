# ci-module: evals
"""Prompt size is gated like quality: a prompt that grows fails CI (#641).

Measured in the test world (no Gmail, Calendar or web connected, one owner, no history), per part and per tier, at
4.0 characters per token (qwen3 measured 4.3 to 4.5 on prompt text and tool schemas; this leans cautious). The hard
targets are the tier promises: compact 2,500 tokens or less, minimal 600 or less. The full tier may not grow more
than 5% over `evals/prompt-size-baseline.json`; a deliberate change updates the baseline in the same PR:
`UPDATE_PROMPT_BASELINE=1 uv run pytest tests/evals/test_prompt_size_gate.py -s`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select

from superaide.brain.llm import FakeLLM
from superaide.brain.models import LlmProvider
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import Script, owner_client, turn_for  # noqa: F401  (fixture)

BASELINE = Path(__file__).resolve().parents[2] / "evals" / "prompt-size-baseline.json"
CHARS_PER_TOKEN = 4.0
FULL_GROWTH = 1.05
TARGETS = {"compact": 2_500, "minimal": 600}
PROBES = {  # tier -> (window, message)
    "full": (200_000, "When is my flight?"),
    "compact": (16_384, "When is my flight?"),
    "minimal": (16_384, "hi"),
}


def tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def parts(call: dict[str, Any]) -> dict[str, int]:
    system = call["system"]
    return {
        "system_rules": tokens(len(system[0].text)),
        "system_state": tokens(len(system[1].text)) if len(system) > 1 else 0,
        "tools": tokens(len(json.dumps(call["tools"]))) if call["tools"] else 0,
        "tool_count": len(call["tools"]),
    }


def measure() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    with session_scope() as s:
        row = s.scalar(select(LlmProvider).where(LlmProvider.key == "ollama"))
        if row is None:
            row = LlmProvider(key="ollama", preset="ollama", base_url="http://localhost:11434/v1")
            s.add(row)
        for tier, (window, text) in PROBES.items():
            row.meta = {"context_window": window}
            s.flush()
            turn = turn_for(s, Script("ok"), text=text)
            turn.model, turn.llm = "ollama::qwen3:8b", FakeLLM(Script("ok"))
            turn.run([{"role": "user", "content": text}])
            got = parts(turn.llm.calls[0])
            got["fixed"] = got["system_rules"] + got["system_state"] + got["tools"]
            out[tier] = got
    return out


def table(now: dict[str, dict[str, int]]) -> str:
    head = f"{'tier':8} {'rules':>7} {'state':>7} {'tools':>7} {'n':>3} {'fixed':>7}  target"
    rows = [
        f"{t:8} {p['system_rules']:>7} {p['system_state']:>7} {p['tools']:>7} {p['tool_count']:>3} {p['fixed']:>7}  "
        f"{TARGETS.get(t, 'baseline +5%')}"
        for t, p in now.items()
    ]
    return "\n".join(["prompt tokens by part (4.0 chars/token), message-independent part only", head, *rows])


def test_prompt_size_stays_inside_each_tiers_promise_and_the_full_prompt_does_not_grow(owner_client):  # noqa: F811
    now = measure()
    print("\n" + table(now))
    if os.environ.get("UPDATE_PROMPT_BASELINE"):
        BASELINE.write_text(json.dumps(now, indent=2) + "\n")
    for tier, limit in TARGETS.items():
        assert now[tier]["fixed"] <= limit, f"{tier} prompt is {now[tier]['fixed']} tokens, promised {limit}"
    assert now["minimal"]["tool_count"] == 0
    base = json.loads(BASELINE.read_text())
    assert now["full"]["fixed"] <= base["full"]["fixed"] * FULL_GROWTH, (
        f"the full prompt grew from {base['full']['fixed']} to {now['full']['fixed']} tokens: "
        "trim it, or update the baseline on purpose (UPDATE_PROMPT_BASELINE=1)"
    )
    for tier in ("compact", "minimal"):
        assert now[tier]["fixed"] <= base[tier]["fixed"] * FULL_GROWTH, f"{tier} grew over its baseline"
