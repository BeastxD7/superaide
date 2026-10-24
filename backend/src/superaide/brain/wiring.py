"""Wire settings into a model: is a real model connected, which provider serves each role, and the LLM itself.

This used to live in `factory.py`, which also builds the `Agent` (and so imports `pipeline`). The callers that only
need a model (knowledge, journal, triage, voice, ...) import this module instead, so they no longer depend on
the pipeline (ADR 0015, phase 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from superaide.brain.budget import BudgetGuard
from superaide.brain.llm import LLM, ClaudeLLM, FakeLLM, ProviderLLM, RoutedLLM, SystemBlock
from superaide.brain.pricing import DEFAULT_PRICES, PriceRegistry, owner_prices
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.config import Settings, get_settings
from superaide.db.session import session_scope
from superaide.tools.web import WebTools


def demo_responder(
    purpose: str, system: list[SystemBlock], messages: list[dict[str, Any]], output_model: type[BaseModel] | None
) -> Any:
    """Offline stand-in used when no Claude API key is set. Costs nothing."""
    body = messages[-1]["content"] if messages else ""
    if isinstance(body, list):  # text blocks (with sources attached): the owner's words come last
        body = next((b.get("text", "") for b in reversed(body) if b.get("type") == "text"), "")
    lines = body.splitlines()
    last = lines[-1] if lines else ""
    return TurnOutput(
        reply=(
            f'(Offline mode: no AI model is connected yet, so I can only echo you.) You said: "{last}". '
            "Choose an AI model on the AI model page to get real replies."
        ),
        requested_action=RequestedAction(kind="none", summary=""),
        prediction=Prediction(expectation="you will reply", confidence=0.5),
    )


@dataclass(frozen=True)
class BrainStatus:
    """Whether Ava has a real model to think with, and which provider it is."""

    ready: bool
    provider: str | None  # the provider in use when ready, e.g. "anthropic"
    reason: str  # connected | fake_llm | no_provider


def credits_on(settings: Settings) -> bool:
    """Superaide AI credits can pay for this owner: the switch is on, there's a key, and they opted in."""
    from superaide.brain import platform_ai

    return settings.use_platform_ai and platform_ai.available()


def with_credits(settings: Settings) -> Settings:
    """Roles the owner's own keys can't serve run on the credits' model, when the owner opted in.
    Their own key always wins for a role it can serve."""
    from superaide.brain.platform_ai import ROUTE
    from superaide.brain.providers.config import split

    if not credits_on(settings):
        return settings
    updates = {}
    for field in ("model_chat", "model_triage", "model_deep"):
        provider, _ = split(getattr(settings, field))
        if not provider_usable(settings, provider):
            updates[field] = f"{ROUTE}::{get_settings().platform_model}"
    return settings.model_copy(update=updates) if updates else settings


def provider_usable(settings: Settings, provider: str) -> bool:
    """Connected, with a key where the service needs one."""
    from superaide.brain.platform_ai import ROUTE

    if provider == ROUTE:
        return credits_on(settings)
    if provider == "anthropic":
        return bool(settings.anthropic_api_key)
    from superaide.brain.providers.presets import PRESETS

    cfg = settings.llm_providers.get(provider)
    preset = PRESETS.get((cfg or {}).get("preset", provider))
    return cfg is not None and preset is not None and (bool(cfg.get("api_key")) or not preset.key_required)


def brain_status(settings: Settings) -> BrainStatus:
    """A real model whenever the chat model's provider is connected, unless FAKE_LLM=true forces offline mode."""
    from superaide.brain.providers.config import split

    if settings.fake_llm:
        return BrainStatus(False, None, "fake_llm")
    provider, _ = split(with_credits(settings).model_chat)
    if provider_usable(settings, provider):
        return BrainStatus(True, provider, "connected")
    return BrainStatus(False, None, "no_provider")


def brain_ready(settings: Settings) -> bool:
    return brain_status(settings).ready


def load_prices(settings: Settings | None = None) -> PriceRegistry:
    """Built-in prices, any the owner set in the app, and where local model servers live."""
    from superaide.brain.providers.config import listed_prices

    with session_scope() as s:
        prices = owner_prices(s)
        listed = listed_prices(s)
    urls = {p: cfg.get("base_url", "") for p, cfg in (settings.llm_providers if settings else {}).items()}
    kinds = {k: cfg.get("preset", k) for k, cfg in (settings.llm_providers if settings else {}).items()}
    return PriceRegistry(prices.owner, base_urls={p: u for p, u in urls.items() if u}, listed=listed, kinds=kinds)


def build_llm(settings: Settings) -> LLM:
    settings = with_credits(settings)
    if not brain_ready(settings):
        return FakeLLM(demo_responder)
    from superaide.brain.providers.config import split
    from superaide.brain.providers.openai_compat import OpenAICompatProvider
    from superaide.brain.providers.presets import PRESETS

    guard = BudgetGuard.for_instance(settings, prices=load_prices(settings))  # this instance's ledger and limits
    web = WebTools(
        guard, search_key=settings.brave_search_api_key, search_usd=Decimal(str(settings.web_search_usd_per_call))
    )
    routes: dict[str, LLM] = {}
    if settings.anthropic_api_key:
        routes["anthropic"] = ClaudeLLM(guard, api_key=settings.anthropic_api_key)
    for key, cfg in settings.llm_providers.items():
        preset = cfg.get("preset", key)
        if preset in PRESETS and provider_usable(settings, key):
            provider = OpenAICompatProvider(
                guard, PRESETS[preset], api_key=cfg.get("api_key", ""), base_url=cfg.get("base_url", ""), id=key
            )
            routes[key] = ProviderLLM(provider, web=web)
    from superaide.brain import platform_ai

    if any(split(m)[0] == platform_ai.ROUTE for m in (settings.model_chat, settings.model_triage, settings.model_deep)):
        # Paid by Superaide AI credits: its own guard records key_source="platform" and reserves against
        # the owner's allowance and the platform's daily cap. The key never enters the owner's settings.
        # Priced from the built-in tables only, never from prices the owner set (BudgetGuard enforces it too).
        credits = BudgetGuard.for_instance(settings, prices=DEFAULT_PRICES, key_source="platform")
        routes[platform_ai.ROUTE] = ClaudeLLM(credits, api_key=platform_ai.platform_key())
    roles = {split(m)[0] for m in (settings.model_chat, settings.model_triage, settings.model_deep)}
    if set(routes) == {"anthropic"} and roles == {"anthropic"}:
        return routes["anthropic"]
    return RoutedLLM(routes, fallback=settings.model_chat)
