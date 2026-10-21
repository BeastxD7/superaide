"""Claude API prices in US dollars per million tokens (checked 21 Oct 2026).

Source: https://platform.claude.com/docs/en/about-claude/pricing
Update this table if Anthropic changes prices; a model missing from it is
refused, so the spend cap can never be bypassed by an unpriced model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

MILLION = Decimal(1_000_000)
CACHE_WRITE_5M = Decimal("1.25")
CACHE_WRITE_1H = Decimal("2")
BATCH_DISCOUNT = Decimal("0.5")
WEB_SEARCH_USD = Decimal("0.01")  # $10 per 1,000 searches


@dataclass(frozen=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    cache_read_mult: Decimal
    # Haiku 5.5 charges more once a prompt passes 100K tokens.
    long_threshold: int | None = None
    long_input: Decimal | None = None
    long_output: Decimal | None = None

    def rates(self, prompt_tokens: int) -> tuple[Decimal, Decimal]:
        if self.long_threshold is not None and prompt_tokens > self.long_threshold:
            assert self.long_input is not None and self.long_output is not None
            return self.long_input, self.long_output
        return self.input, self.output


PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": ModelPrice(Decimal("10"), Decimal("50"), Decimal("0.025")),
    "claude-opus-5-5": ModelPrice(Decimal("4"), Decimal("20"), Decimal("0.05")),
    "claude-sonnet-5-5": ModelPrice(Decimal("2"), Decimal("10"), Decimal("0.05")),
    "claude-haiku-5-5": ModelPrice(
        Decimal("0.10"),
        Decimal("0.50"),
        Decimal("0.10"),
        long_threshold=100_000,
        long_input=Decimal("0.50"),
        long_output=Decimal("2.50"),
    ),
}


class UnknownModelPrice(KeyError):
    pass


def price_for(model: str) -> ModelPrice:
    try:
        return PRICES[model]
    except KeyError as exc:
        raise UnknownModelPrice(f"No price for model {model!r}; add it to pricing.py first") from exc


@dataclass(frozen=True)
class UsageCounts:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    web_search_requests: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_5m_tokens + self.cache_write_1h_tokens

    @classmethod
    def from_api(cls, usage: Any) -> UsageCounts:
        """Read an anthropic.types.Usage (or a dict with the same fields)."""

        def get(obj: Any, name: str) -> Any:
            if obj is None:
                return None
            return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

        creation = get(usage, "cache_creation")
        w5 = get(creation, "ephemeral_5m_input_tokens")
        w1 = get(creation, "ephemeral_1h_input_tokens")
        total_write = get(usage, "cache_creation_input_tokens") or 0
        if w5 is None and w1 is None:
            w5, w1 = total_write, 0  # no breakdown: price it all as 5-minute writes
        server = get(usage, "server_tool_use")
        return cls(
            input_tokens=get(usage, "input_tokens") or 0,
            output_tokens=get(usage, "output_tokens") or 0,
            cache_read_tokens=get(usage, "cache_read_input_tokens") or 0,
            cache_write_5m_tokens=w5 or 0,
            cache_write_1h_tokens=w1 or 0,
            web_search_requests=get(server, "web_search_requests") or 0,
        )


def cost_usd(model: str, usage: UsageCounts, *, batch: bool = False) -> Decimal:
    price = price_for(model)
    rate_in, rate_out = price.rates(usage.prompt_tokens)
    token_cost = (
        Decimal(usage.input_tokens) * rate_in
        + Decimal(usage.cache_read_tokens) * rate_in * price.cache_read_mult
        + Decimal(usage.cache_write_5m_tokens) * rate_in * CACHE_WRITE_5M
        + Decimal(usage.cache_write_1h_tokens) * rate_in * CACHE_WRITE_1H
        + Decimal(usage.output_tokens) * rate_out
    ) / MILLION
    if batch:
        token_cost *= BATCH_DISCOUNT
    return token_cost + Decimal(usage.web_search_requests) * WEB_SEARCH_USD


# Tokens the API adds that we cannot see in our own prompt (structured-output
# instructions, tool scaffolding, message framing). Generous on purpose.
HIDDEN_OVERHEAD_TOKENS = 2_000


def worst_case_cost(model: str, prompt_chars: int, max_tokens: int, *, cache_write: bool = True) -> Decimal:
    """Upper bound for one call: about 2 characters per token (real text averages
    nearer 4), plus hidden overhead, with the whole prompt priced as a 1-hour
    cache write (2x) and every allowed output token used."""
    tokens_in = math.ceil(prompt_chars / 2) + HIDDEN_OVERHEAD_TOKENS
    price = price_for(model)
    rate_in, rate_out = price.rates(tokens_in)
    mult = CACHE_WRITE_1H if cache_write else Decimal(1)
    return (Decimal(tokens_in) * rate_in * mult + Decimal(max_tokens) * rate_out) / MILLION
