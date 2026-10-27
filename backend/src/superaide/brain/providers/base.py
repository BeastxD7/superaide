"""The interface every model provider implements, and the one spend path they all share."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from superaide.brain.providers.types import (
    Capabilities,
    Completion,
    Effort,
    Message,
    OnStreamEvent,
    Step,
    SystemBlock,
    Tool,
)
from superaide.brain.spend.pricing import UsageCounts


class Guard(Protocol):
    """What a provider needs from the budget guard (brain/budget.py BudgetGuard)."""

    def reserve(self, *, purpose: str, model: str, worst_case_usd: Decimal, provider: str = ...) -> uuid.UUID: ...

    def settle(
        self,
        entry_id: uuid.UUID,
        *,
        model: str,
        usage: UsageCounts,
        request_id: str | None,
        provider: str = ...,
        reported_cost_usd: Decimal | None = None,
        known: bool = True,
    ) -> Decimal: ...

    def fail(self, entry_id: uuid.UUID, error: str) -> None: ...


@dataclass
class Priced:
    """What a response says about its own cost."""

    usage: UsageCounts
    request_id: str | None = None
    reported_cost_usd: Decimal | None = None  # e.g. OpenRouter's usage.cost
    known: bool = True  # False when the provider sent no usage


@dataclass
class CheckReport:
    ok: bool
    message: str
    models: list[str] = field(default_factory=list)
    # Prices the service lists for its models, US$ per million tokens: {model: {"input", "output", "cache_read"}}
    prices: dict[str, dict[str, str]] = field(default_factory=dict)


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
        tools: list[Tool],
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


def _note_rejected_key(guard: Guard, provider: str, exc: BaseException) -> None:
    """A provider that refuses the key is recorded for this instance (brain/keyhealth.py), and the
    error is tagged so every caller can tell it from a passing failure."""
    from superaide.brain import keyhealth

    if not keyhealth.is_auth_error(exc):
        return
    label = "credits" if getattr(guard, "key_source", "owner") == "platform" else provider
    keyhealth.mark(exc, label)
    scope = getattr(guard, "scope", None)
    if scope is None:
        keyhealth.record_rejected(label)
        return
    from superaide.platform.tenancy.context import use_instance

    with use_instance(scope):
        keyhealth.record_rejected(label)


def mark_billable[E: BaseException](exc: E) -> E:
    """Say that this error came after the provider began to answer (e.g. a stream that dropped midway), so the
    call may have been billed: the guard settles it at its reservation instead of recording it as free."""
    exc.billable = True  # type: ignore[attr-defined]
    return exc


def metered[R](
    guard: Guard,
    *,
    purpose: str,
    model: str,
    worst_case_usd: Decimal,
    call: Callable[[], R],
    priced: Callable[[R], Priced],
    provider: str = "anthropic",
) -> tuple[R, UsageCounts, Decimal]:
    """Reserve the worst case, make the call, settle at the real price. A call that raises is
    marked failed (nothing billed) unless it failed after the answer began (`mark_billable`), when it is
    settled at its reservation; a response that came back is settled before anything reads
    it, so a cut-off or invalid answer is still counted against the cap."""
    entry_id = guard.reserve(purpose=purpose, model=model, worst_case_usd=worst_case_usd, provider=provider)
    try:
        response = call()
    except Exception as exc:
        _note_rejected_key(guard, provider, exc)
        if getattr(exc, "billable", False):  # cut off mid-answer: the provider may have charged for it
            guard.settle(entry_id, model=model, usage=UsageCounts(), request_id=None, provider=provider, known=False)
        else:  # the provider did not return a billable response
            guard.fail(entry_id, f"{type(exc).__name__}: {exc}")
        raise
    p = priced(response)
    cost = guard.settle(
        entry_id, model=model, usage=p.usage, request_id=p.request_id, provider=provider,
        reported_cost_usd=p.reported_cost_usd, known=p.known,
    )  # fmt: skip
    return response, p.usage, cost
