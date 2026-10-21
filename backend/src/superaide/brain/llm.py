"""Claude client wrapper. Every call goes through the budget guard.

ClaudeLLM talks to the real API. FakeLLM returns scripted outputs so tests
and offline demos cost nothing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from superaide.brain.budget import BudgetGuard
from superaide.brain.pricing import WEB_SEARCH_USD, UsageCounts, worst_case_cost

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)


@dataclass
class SystemBlock:
    text: str
    cache: bool = False  # mark the end of the stable prefix for prompt caching


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


def _prompt_chars(
    system: list[SystemBlock], messages: list[dict[str, Any]], output_model: type[BaseModel] | None
) -> int:
    chars = sum(len(b.text) for b in system) + len(json.dumps(messages, default=str))
    if output_model is not None:
        chars += len(json.dumps(output_model.model_json_schema()))
    return chars


def _system_param(system: list[SystemBlock]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in system:
        item: dict[str, Any] = {"type": "text", "text": block.text}
        if block.cache:
            item["cache_control"] = {"type": "ephemeral"}
        out.append(item)
    return out


class ClaudeLLM:
    def __init__(self, guard: BudgetGuard, api_key: str = "", client: Any = None) -> None:
        if client is None:
            import anthropic

            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set; add it to .env or set FAKE_LLM=true")
            client = anthropic.Anthropic(api_key=api_key, max_retries=2)
        self.client = client
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
    ) -> StepResult:
        prompt_chars = _prompt_chars(system, messages, None) + len(json.dumps(tools))
        worst = worst_case_cost(model, prompt_chars, max_tokens) + Decimal(max_web_searches) * WEB_SEARCH_USD
        entry_id = self.guard.reserve(purpose=purpose, model=model, worst_case_usd=worst)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": _system_param(system),
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if effort:
            kwargs["output_config"] = {"effort": effort}
        try:
            response = self.client.messages.create(**kwargs)
        except Exception as exc:
            self.guard.fail(entry_id, f"{type(exc).__name__}: {exc}")
            raise
        usage = UsageCounts.from_api(response.usage)
        cost = self.guard.settle(entry_id, model=model, usage=usage, request_id=getattr(response, "_request_id", None))
        content = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b) for b in response.content]
        return StepResult(content=content, stop_reason=response.stop_reason or "end_turn", usage=usage, cost_usd=cost)

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
        worst = worst_case_cost(model, _prompt_chars(system, messages, output_model), max_tokens)
        entry_id = self.guard.reserve(purpose=purpose, model=model, worst_case_usd=worst)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": _system_param(system),
            "messages": messages,
        }
        output_config: dict[str, Any] = {}
        if effort:
            output_config["effort"] = effort
        if output_model is not None:
            from anthropic import transform_schema

            output_config["format"] = {
                "type": "json_schema",
                "schema": transform_schema(output_model.model_json_schema()),
            }
        if output_config:
            kwargs["output_config"] = output_config

        try:
            response = self.client.messages.create(**kwargs)
        except Exception as exc:  # the API did not return a billable response
            self.guard.fail(entry_id, f"{type(exc).__name__}: {exc}")
            raise

        # Settle before looking at the output: a cut-off or invalid answer is still billed.
        usage = UsageCounts.from_api(response.usage)
        cost = self.guard.settle(entry_id, model=model, usage=usage, request_id=getattr(response, "_request_id", None))
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        stop_reason = getattr(response, "stop_reason", None)
        parsed = None
        if output_model is not None:
            if stop_reason == "max_tokens":
                log.warning("%s: output cut off at max_tokens=%s; nothing parsed", purpose, max_tokens)
            else:
                try:
                    parsed = output_model.model_validate_json(text)
                except ValidationError as exc:
                    log.warning(
                        "%s: output did not match %s (stop_reason=%s): %s",
                        purpose,
                        output_model.__name__,
                        stop_reason,
                        exc,
                    )
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
