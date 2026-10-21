"""The interface every model provider implements, and the one spend path they all share."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from superaide.brain.pricing import UsageCounts
from superaide.brain.providers.types import (
    Capabilities,
    Completion,
    Effort,
    HostedTool,
    Message,
    OnStreamEvent,
    Step,
    SystemBlock,
    ToolSpec,
)


class Guard(Protocol):
    """What a provider needs from the budget guard (brain/budget.py BudgetGuard)."""

    def reserve(self, *, purpose: str, model: str, worst_case_usd: Decimal) -> uuid.UUID: ...

    def settle(self, entry_id: uuid.UUID, *, model: str, usage: UsageCounts, request_id: str | None) -> Decimal: ...

    def fail(self, entry_id: uuid.UUID, error: str) -> None: ...


@dataclass
class CheckReport:
    ok: bool
    message: str
    models: list[str] = field(default_factory=list)


class LLMProvider(Protocol):
    id: str  # the configured instance, e.g. "anthropic"
    kind: str  # the adapter: anthropic | openai_compat | fake

    def capabilities(self, model: str) -> Capabilities: ...

    def step(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[Message],
        tools: list[ToolSpec],
        hosted: list[HostedTool],
        max_tokens: int,
        effort: Effort | None = None,
        on_event: OnStreamEvent | None = None,
    ) -> Step: ...

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[Message],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: Effort | None = None,
    ) -> Completion: ...


def metered[R](
    guard: Guard,
    *,
    purpose: str,
    model: str,
    worst_case_usd: Decimal,
    call: Callable[[], R],
    priced: Callable[[R], tuple[UsageCounts, str | None]],
) -> tuple[R, UsageCounts, Decimal]:
    """Reserve the worst case, make the call, settle at the real price. A call that raises is
    marked failed (nothing billed); a response that came back is settled before anything reads
    it, so a cut-off or invalid answer is still counted against the cap."""
    entry_id = guard.reserve(purpose=purpose, model=model, worst_case_usd=worst_case_usd)
    try:
        response = call()
    except Exception as exc:  # the provider did not return a billable response
        guard.fail(entry_id, f"{type(exc).__name__}: {exc}")
        raise
    usage, request_id = priced(response)
    cost = guard.settle(entry_id, model=model, usage=usage, request_id=request_id)
    return response, usage, cost
