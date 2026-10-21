"""Claude client wrapper. Every call goes through the budget guard.

ClaudeLLM talks to the real API. FakeLLM returns scripted outputs so tests
and offline demos cost nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from agi_agent.brain.budget import BudgetGuard
from agi_agent.brain.pricing import UsageCounts, worst_case_cost

T = TypeVar("T", bound=BaseModel)


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


class LLM(Protocol):
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


class ClaudeLLM:
    def __init__(self, guard: BudgetGuard, api_key: str = "", client: Any = None) -> None:
        if client is None:
            import anthropic

            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set; add it to .env or set FAKE_LLM=true")
            client = anthropic.Anthropic(api_key=api_key, max_retries=2)
        self.client = client
        self.guard = guard

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

        system_param: list[dict[str, Any]] = []
        for block in system:
            item: dict[str, Any] = {"type": "text", "text": block.text}
            if block.cache:
                item["cache_control"] = {"type": "ephemeral"}
            system_param.append(item)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_param,
            "messages": messages,
        }
        if effort:
            kwargs["output_config"] = {"effort": effort}

        try:
            if output_model is not None:
                response = self.client.messages.parse(output_format=output_model, **kwargs)
                parsed = getattr(response, "parsed_output", None)
            else:
                response = self.client.messages.create(**kwargs)
                parsed = None
        except Exception as exc:  # the API did not return a billable response
            self.guard.fail(entry_id, f"{type(exc).__name__}: {exc}")
            raise

        usage = UsageCounts.from_api(response.usage)
        cost = self.guard.settle(entry_id, model=model, usage=usage, request_id=getattr(response, "_request_id", None))
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        return LLMResult(text=text, parsed=parsed, model=model, usage=usage, cost_usd=cost)


Responder = Callable[[str, list[SystemBlock], list[dict[str, Any]], type[BaseModel] | None], Any]


@dataclass
class FakeLLM:
    """Scripted model. `responder` returns a string, a dict or a Pydantic object."""

    responder: Responder
    calls: list[dict[str, Any]] = field(default_factory=list)

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
