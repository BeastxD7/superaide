"""Helpers for the rehearsal tests (#110): a model whose behaviour depends on the model name and the prompt,
and a way to put real past tasks in the owner's history."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from superaide.brain.llm import StepResult
from superaide.brain.pricing import UsageCounts
from tests.pipeline.test_loop import Script, tool_use, turn_for

Responder = Callable[[str, str, list[dict[str, Any]]], Any]  # (model, system text, messages) -> text or blocks


class SwitchLLM:
    """A fake model for the owner loop. `fn` sees the model name, the system prompt and the messages."""

    def __init__(self, fn: Responder) -> None:
        self.fn = fn
        self.calls: list[dict[str, Any]] = []

    def step(
        self, *, purpose, model, system, messages, tools, max_tokens, effort=None, max_web_searches=0, on_event=None
    ):
        self.calls.append({"purpose": purpose, "model": model, "system": system, "messages": messages})
        out = self.fn(model, "\n".join(b.text for b in system), messages)
        content = out if isinstance(out, list) else [{"type": "text", "text": out}]
        stop = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
        return StepResult(content=content, stop_reason=stop, usage=UsageCounts())

    def complete(self, **kw):  # the owner loop does not use it here
        raise NotImplementedError


def results_seen(messages: list[dict[str, Any]]) -> int:
    """How many tool results the loop has already sent back (which step of the turn this is)."""
    return sum(
        1
        for m in messages
        if isinstance(m.get("content"), list) and any(b.get("type") == "tool_result" for b in m["content"])
    )


def live_history(s, n: int) -> list[Any]:
    """The owner asks `n` things; each time Ava recalls, then sets a reminder (recorded as hashes)."""
    ids = []
    for i in range(n):
        script = Script(
            tool_use("recall", {"query": f"flight {i}"}, "t1"),
            tool_use("create_reminder", {"task": f"Call person {i}", "when": "tomorrow at 5pm"}, "t2"),
            "Done.",
        )
        turn = turn_for(s, script, f"Please sort out item {i}")
        turn.run([{"role": "user", "content": "x"}])
        ids.append(turn.inbound.id)
    return ids


def switch(agent, fn: Responder) -> SwitchLLM:
    agent.llm = SwitchLLM(fn)
    return agent.llm
