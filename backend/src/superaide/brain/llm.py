"""The model interface the tool loop uses today (Messages API dicts). Every call goes through the budget guard.

ClaudeLLM is a thin shim over the Anthropic provider (brain/providers/anthropic.py). FakeLLM returns
scripted outputs so tests and offline demos cost nothing. See docs/decisions/0007-pluggable-providers.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from superaide.brain.budget import BudgetGuard
from superaide.brain.pricing import UsageCounts
from superaide.brain.providers.types import SystemBlock

T = TypeVar("T", bound=BaseModel)

# Live progress from a streaming step: ("text", {"delta"}), ("thinking", {"delta"}), and
# ("block", {"block"}) as each content block completes (server tool calls and results).
OnEvent = Callable[[str, dict[str, Any]], None]


@dataclass
class LLMResult:
    text: str
    parsed: Any
    model: str
    usage: UsageCounts
    cost_usd: Decimal = Decimal(0)
    stop_reason: str | None = None


@dataclass
class StepResult:
    """One Messages API call inside a tool loop: content blocks as plain dicts (sent back
    unchanged on the next step, as server tools require), why it stopped, and its cost."""

    content: list[dict[str, Any]]
    stop_reason: str
    usage: UsageCounts
    cost_usd: Decimal = Decimal(0)


class LLM(Protocol):
    def step(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        effort: str | None = None,
        max_web_searches: int = 0,
        on_event: OnEvent | None = None,
    ) -> StepResult: ...

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: str | None = None,
    ) -> LLMResult: ...


class ClaudeLLM:
    """The tool loop's dict interface on top of the Anthropic provider (brain/providers/anthropic.py).
    Kept until the loop speaks neutral parts; requests are identical either way."""

    def __init__(self, guard: BudgetGuard, api_key: str = "", client: Any = None) -> None:
        from superaide.brain.providers.anthropic import AnthropicProvider

        self.provider = AnthropicProvider(guard, api_key=api_key, client=client)
        self.client = self.provider.client
        self.guard = guard

    def step(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        effort: str | None = None,
        max_web_searches: int = 0,
        on_event: OnEvent | None = None,
    ) -> StepResult:
        content, stop_reason, usage, cost = self.provider.step_wire(
            purpose=purpose, model=model, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
            effort=effort, max_web_searches=max_web_searches, on_event=on_event,
        )  # fmt: skip
        return StepResult(content=content, stop_reason=stop_reason, usage=usage, cost_usd=cost)

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: str | None = None,
    ) -> LLMResult:
        text, parsed, usage, cost, stop_reason = self.provider.complete_wire(
            purpose=purpose, model=model, system=system, messages=messages, max_tokens=max_tokens,
            output_model=output_model, effort=effort,
        )  # fmt: skip
        return LLMResult(text=text, parsed=parsed, model=model, usage=usage, cost_usd=cost, stop_reason=stop_reason)


Responder = Callable[[str, list[SystemBlock], list[dict[str, Any]], type[BaseModel] | None], Any]


@dataclass
class FakeLLM:
    """Scripted model. `responder` returns a string, a dict or a Pydantic object."""

    responder: Responder
    calls: list[dict[str, Any]] = field(default_factory=list)

    def step(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        effort: str | None = None,
        max_web_searches: int = 0,
        on_event: OnEvent | None = None,
    ) -> StepResult:
        """The responder gets the purpose, system and messages; it may return text, a
        list of content blocks, a full {"content", "stop_reason"} dict, or a TurnOutput
        (its reply becomes the text, so the offline echo works in the tool loop too)."""
        self.calls.append({"purpose": purpose, "model": model, "system": system, "messages": messages, "tools": tools})
        out = self.responder(purpose, system, messages, None)
        if isinstance(out, dict) and "content" in out:
            content, stop = list(out["content"]), str(out.get("stop_reason", "end_turn"))
        elif isinstance(out, list):
            content = list(out)
            stop = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
        else:
            text = out.reply if hasattr(out, "reply") else str(out)
            content, stop = [{"type": "text", "text": text}], "end_turn"
        if on_event is not None:
            for block in content:
                if block.get("type") == "text":
                    on_event("text", {"delta": block["text"]})
                elif block.get("type") == "thinking":
                    on_event("thinking", {"delta": block.get("thinking", "")})
                on_event("block", {"block": block})
        return StepResult(content=content, stop_reason=stop, usage=UsageCounts())

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: str | None = None,
    ) -> LLMResult:
        self.calls.append({"purpose": purpose, "model": model, "system": system, "messages": messages})
        out = self.responder(purpose, system, messages, output_model)
        parsed = None
        if output_model is not None:
            parsed = out if isinstance(out, output_model) else output_model.model_validate(out)
            text = parsed.model_dump_json()
        else:
            text = str(out)
        return LLMResult(text=text, parsed=parsed, model=model, usage=UsageCounts())
