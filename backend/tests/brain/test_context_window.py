# ci-module: brain
"""The context window of each model, the input budget, and the estimate used to spot a cut prompt (#641)."""

from __future__ import annotations

import pytest

from superaide.brain import context_window as cw
from superaide.brain.llm import SystemBlock
from superaide.brain.providers import ollama_ctx


@pytest.mark.parametrize(
    ("model", "window"),
    [
        ("claude-sonnet-5-5", 200_000),
        ("anthropic/claude-haiku-5-5", 200_000),
        ("gpt-4.1-mini", 1_047_576),
        ("gpt-4o", 128_000),
        ("gpt-4", 8_192),
        ("gemini-2.5-flash", 1_048_576),
    ],
)
def test_hosted_models_come_from_the_table(model, window):
    info = cw.resolve_window(model, provider="openai")
    assert (info.tokens, info.source) == (window, "table")


def test_an_unknown_hosted_model_is_assumed_modest_and_a_local_one_small():
    assert cw.resolve_window("acme-xl", provider="openrouter") == cw.WindowInfo(cw.DEFAULT_HOSTED, "default")
    assert cw.resolve_window("qwen3:8b", provider="ollama", probe=False) == cw.WindowInfo(cw.DEFAULT_LOCAL, "default")
    assert cw.resolve_window("gpt-4o", provider="vllm").source == "default"  # a local server's name proves nothing


def test_the_owner_override_wins_over_everything():
    assert cw.resolve_window("claude-sonnet-5-5", provider="anthropic", override=64_000) == cw.WindowInfo(
        64_000, "override"
    )


def test_ollama_reports_the_window_actually_in_use(monkeypatch):
    ollama_ctx.forget()
    monkeypatch.setattr(ollama_ctx, "context_tokens", lambda base, model, **kw: 16_384)
    assert cw.resolve_window("qwen3:8b", provider="ollama", base_url="http://localhost:11434/v1") == cw.WindowInfo(
        16_384, "probe"
    )
    monkeypatch.setattr(ollama_ctx, "context_tokens", lambda base, model, **kw: None)  # the server did not say
    assert cw.resolve_window("qwen3:8b", provider="ollama", base_url="x").source == "default"


def test_the_input_budget_is_window_minus_the_answer_minus_ten_percent():
    assert cw.input_budget(4_096, 1_200) == 4_096 - 1_200 - 410
    assert cw.input_budget(200_000, 1_200) == 200_000 - 1_200 - 20_000
    assert cw.input_budget(1_000, 5_000) == 0  # never negative


def test_the_estimate_is_conservative_and_counts_system_messages_and_tools():
    text = "x" * 3_500
    assert cw.estimate_tokens(text) == 1_000
    big = cw.estimate_request([SystemBlock("a" * 7_000)], [{"role": "user", "content": "hi"}], [{"name": "t"}])
    small = cw.estimate_request([SystemBlock("a" * 7_000)], [{"role": "user", "content": "hi"}], [])
    assert big > small >= 2_000


@pytest.mark.parametrize(
    ("sent", "reported", "cut"),
    [
        (7_000, 2_050, True),  # Ollama kept the last ~2,050 tokens of a 7,000-token prompt
        (7_000, 5_800, False),  # the estimate is conservative: a 20% gap is normal
        (7_000, 0, False),  # the server did not report: no verdict
        (300, 100, False),  # a short prompt's rounding is never a cut
        (7_000, 4_000, True),
    ],
)
def test_a_cut_prompt_is_one_the_server_counted_far_below_what_was_sent(sent, reported, cut):
    assert cw.truncated(sent, reported) is cut
