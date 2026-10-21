"""Model prices in US dollars per million tokens, per provider and model.

Built-in tables are checked against the providers' published prices; a model with no price is
refused, so the spend cap can never be bypassed by an unpriced model. The owner can set a price
for a model we don't list (stored in `owner_model_price`, see `owner_prices`), and a model
served by a local server kind (Ollama, vLLM, a custom endpoint) whose address is on this machine
or the local network costs 0. See docs/decisions/0007-pluggable-providers.md, section 7.

Anthropic: https://platform.claude.com/docs/en/about-claude/pricing (checked 21 Oct 2026).
Other providers get their tables with their adapters, each with a checked date and source.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

MILLION = Decimal(1_000_000)
CACHE_WRITE_5M = Decimal("1.25")
CACHE_WRITE_1H = Decimal("2")
BATCH_DISCOUNT = Decimal("0.5")
WEB_SEARCH_USD = Decimal("0.01")  # Anthropic: $10 per 1,000 searches
LOCAL_KINDS = frozenset({"ollama", "vllm", "custom"})


@dataclass(frozen=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    cache_read_mult: Decimal = Decimal(1)
    # Explicit prompt-cache writes cost a multiple of input (Anthropic); 1 where caching is automatic or absent.
    cache_write_5m_mult: Decimal = Decimal(1)
    cache_write_1h_mult: Decimal = Decimal(1)
    # Per-call fees for provider-run tools, e.g. {"web_search": 0.01}.
    tool_fees: dict[str, Decimal] = field(default_factory=dict)
    # Haiku 5.5 charges more once a prompt passes 100K tokens.
    long_threshold: int | None = None
    long_input: Decimal | None = None
    long_output: Decimal | None = None

    def rates(self, prompt_tokens: int) -> tuple[Decimal, Decimal]:
        if self.long_threshold is not None and prompt_tokens > self.long_threshold:
            assert self.long_input is not None and self.long_output is not None
            return self.long_input, self.long_output
        return self.input, self.output

    @property
    def worst_cache_write_mult(self) -> Decimal:
        return max(Decimal(1), self.cache_write_5m_mult, self.cache_write_1h_mult)


def _claude(inp: str, out: str, read: str, **long: Any) -> ModelPrice:
    return ModelPrice(
        Decimal(inp),
        Decimal(out),
        Decimal(read),
        CACHE_WRITE_5M,
        CACHE_WRITE_1H,
        {"web_search": WEB_SEARCH_USD},
        **long,
    )


PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": _claude("10", "50", "0.025"),
    "claude-opus-5-5": _claude("4", "20", "0.05"),
    "claude-sonnet-5-5": _claude("2", "10", "0.05"),
    "claude-haiku-5-5": _claude(
        "0.10", "0.50", "0.10", long_threshold=100_000, long_input=Decimal("0.50"), long_output=Decimal("2.50")
    ),
}
FREE = ModelPrice(Decimal(0), Decimal(0))

# What every model of a provider kind is charged besides tokens, applied to owner-set prices too,
# so an owner price for a new Claude model still counts cache writes and web searches.
PROVIDER_RULES: dict[str, dict[str, Any]] = {
    "anthropic": {
        "cache_write_5m_mult": CACHE_WRITE_5M,
        "cache_write_1h_mult": CACHE_WRITE_1H,
        "tool_fees": {"web_search": WEB_SEARCH_USD},
    },
}

# Built-in prices by (provider kind, model).
BUILTIN: dict[tuple[str, str], ModelPrice] = {("anthropic", m): p for m, p in PRICES.items()}


class UnknownModelPrice(KeyError):
    pass


def is_local_url(base_url: str | None) -> bool:
    """True only if every address the host resolves to is loopback or private (this machine or the LAN)."""
    if not base_url:
        return False
    from superaide.security.urls import UnsafeURL, resolve

    host = urlsplit(base_url).hostname
    if not host:
        return False
    try:
        ips = resolve(host)
    except UnsafeURL, ValueError:
        return False
    return bool(ips) and all((ip.is_loopback or ip.is_private) and not ip.is_link_local for ip in ips)


@dataclass(frozen=True)
class PriceRegistry:
    """Built-in prices plus the owner's own. Lookups never guess: no price, no call."""

    owner: dict[tuple[str, str], ModelPrice] = field(default_factory=dict)
    # Where each configured local server lives, so its models are free only at a local address.
    base_urls: dict[str, str] = field(default_factory=dict)

    def price_for(self, model: str, provider: str = "anthropic", *, base_url: str | None = None) -> ModelPrice:
        base_url = base_url or self.base_urls.get(provider)
        key = (provider, model)
        if key in self.owner:
            return self.owner[key]
        if key in BUILTIN:
            return BUILTIN[key]
        if provider in LOCAL_KINDS and is_local_url(base_url):
            return FREE
        raise UnknownModelPrice(f"No price for {provider} model {model!r}; set one in the app or add it to pricing.py")


DEFAULT_PRICES = PriceRegistry()


def price_for(model: str, provider: str = "anthropic", *, prices: PriceRegistry | None = None) -> ModelPrice:
    return (prices or DEFAULT_PRICES).price_for(model, provider)


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


def cost_usd(
    model: str,
    usage: UsageCounts,
    *,
    batch: bool = False,
    provider: str = "anthropic",
    prices: PriceRegistry | None = None,
) -> Decimal:
    price = price_for(model, provider, prices=prices)
    rate_in, rate_out = price.rates(usage.prompt_tokens)
    token_cost = (
        Decimal(usage.input_tokens) * rate_in
        + Decimal(usage.cache_read_tokens) * rate_in * price.cache_read_mult
        + Decimal(usage.cache_write_5m_tokens) * rate_in * price.cache_write_5m_mult
        + Decimal(usage.cache_write_1h_tokens) * rate_in * price.cache_write_1h_mult
        + Decimal(usage.output_tokens) * rate_out
    ) / MILLION
    if batch:
        token_cost *= BATCH_DISCOUNT
    return token_cost + Decimal(usage.web_search_requests) * price.tool_fees.get("web_search", Decimal(0))


# Tokens the API adds that we cannot see in our own prompt (structured-output
# instructions, tool scaffolding, message framing). Generous on purpose.
HIDDEN_OVERHEAD_TOKENS = 2_000


def worst_case_cost(
    model: str,
    prompt_chars: int,
    max_tokens: int,
    *,
    cache_write: bool = True,
    provider: str = "anthropic",
    prices: PriceRegistry | None = None,
) -> Decimal:
    """Upper bound for one call: about 2 characters per token (real text averages nearer 4),
    plus hidden overhead, with the whole prompt priced as the dearest cache write and every
    allowed output token used. Sound only when `max_tokens` also bounds reasoning tokens
    (Capabilities.max_output_tokens_bounds_reasoning)."""
    tokens_in = math.ceil(prompt_chars / 2) + HIDDEN_OVERHEAD_TOKENS
    price = price_for(model, provider, prices=prices)
    rate_in, rate_out = price.rates(tokens_in)
    mult = price.worst_cache_write_mult if cache_write else Decimal(1)
    return (Decimal(tokens_in) * rate_in * mult + Decimal(max_tokens) * rate_out) / MILLION


def tool_fee(model: str, tool: str, provider: str = "anthropic", *, prices: PriceRegistry | None = None) -> Decimal:
    return price_for(model, provider, prices=prices).tool_fees.get(tool, Decimal(0))


# --- the owner's own prices ----------------------------------------------------------


def owner_prices(session: Any) -> PriceRegistry:
    """A registry with the prices the owner set in the app."""
    from sqlalchemy import select

    from superaide.db.models import OwnerModelPrice

    rows = session.scalars(select(OwnerModelPrice)).all()
    return PriceRegistry(
        {
            (r.provider, r.model): ModelPrice(
                r.input_usd_per_mtok, r.output_usd_per_mtok, r.cache_read_mult, **PROVIDER_RULES.get(r.provider, {})
            )
            for r in rows
        }
    )


def set_owner_price(
    session: Any,
    *,
    provider: str,
    model: str,
    input_usd_per_mtok: Decimal,
    output_usd_per_mtok: Decimal,
    cache_read_mult: Decimal = Decimal(1),
    user_id: uuid.UUID | None,
) -> None:
    """Record the owner's price for a model. Negative prices are refused; 0 means the owner says it's free."""
    from sqlalchemy import select

    from superaide.audit import audit
    from superaide.db.models import OwnerModelPrice

    if min(input_usd_per_mtok, output_usd_per_mtok, cache_read_mult) < 0:
        raise ValueError("prices can't be negative")
    row = session.scalar(
        select(OwnerModelPrice).where(OwnerModelPrice.provider == provider, OwnerModelPrice.model == model)
    )
    if row is None:
        row = OwnerModelPrice(provider=provider, model=model)
        session.add(row)
    row.input_usd_per_mtok = input_usd_per_mtok
    row.output_usd_per_mtok = output_usd_per_mtok
    row.cache_read_mult = cache_read_mult
    row.set_by_user_id = user_id
    row.updated_at = datetime.now(UTC)
    session.flush()
    audit(
        session, "budget", "model_price_set", provider=provider, model=model,
        input_usd_per_mtok=float(input_usd_per_mtok), output_usd_per_mtok=float(output_usd_per_mtok), user_id=user_id,
    )  # fmt: skip
