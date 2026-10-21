"""Wire settings into a ready Agent."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from superaide.brain.budget import BudgetGuard, Limits
from superaide.brain.llm import LLM, ClaudeLLM, FakeLLM, ProviderLLM, RoutedLLM, SystemBlock
from superaide.brain.pricing import PriceRegistry, owner_prices
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.channels.base import FakeMessenger, Messenger, WebMessenger
from superaide.config import Settings, get_settings
from superaide.db.session import session_scope
from superaide.pipeline import Agent
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
            f'(Offline mode: no Claude API key yet, so I can only echo you.) You said: "{last}". '
            "Connect Claude under Connections to get real replies."
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


def provider_usable(settings: Settings, provider: str) -> bool:
    """Connected, with a key where the service needs one."""
    if provider == "anthropic":
        return bool(settings.anthropic_api_key)
    from superaide.brain.providers.presets import PRESETS

    cfg = settings.llm_providers.get(provider)
    preset = PRESETS.get(provider)
    return cfg is not None and preset is not None and (bool(cfg.get("api_key")) or not preset.key_required)


def brain_status(settings: Settings) -> BrainStatus:
    """A real model whenever the chat model's provider is connected, unless FAKE_LLM=true forces offline mode."""
    from superaide.brain.providers.config import split

    if settings.fake_llm:
        return BrainStatus(False, None, "fake_llm")
    provider, _ = split(settings.model_chat)
    if provider_usable(settings, provider):
        return BrainStatus(True, provider, "connected")
    return BrainStatus(False, None, "no_provider")


def brain_ready(settings: Settings) -> bool:
    return brain_status(settings).ready


def load_prices(settings: Settings | None = None) -> PriceRegistry:
    """Built-in prices, any the owner set in the app, and where local model servers live."""
    with session_scope() as s:
        prices = owner_prices(s)
    urls = {p: cfg.get("base_url", "") for p, cfg in (settings.llm_providers if settings else {}).items()}
    return PriceRegistry(prices.owner, base_urls={p: u for p, u in urls.items() if u})


def build_llm(settings: Settings) -> LLM:
    if not brain_ready(settings):
        return FakeLLM(demo_responder)
    from superaide.brain.providers.config import split
    from superaide.brain.providers.openai_compat import OpenAICompatProvider
    from superaide.brain.providers.presets import PRESETS

    guard = BudgetGuard(settings.max_spend_usd, limits=Limits.from_settings(settings), prices=load_prices(settings))
    web = WebTools(
        guard, search_key=settings.brave_search_api_key, search_usd=Decimal(str(settings.web_search_usd_per_call))
    )
    routes: dict[str, LLM] = {}
    if settings.anthropic_api_key:
        routes["anthropic"] = ClaudeLLM(guard, api_key=settings.anthropic_api_key)
    for preset, cfg in settings.llm_providers.items():
        if preset in PRESETS and provider_usable(settings, preset):
            provider = OpenAICompatProvider(
                guard, PRESETS[preset], api_key=cfg.get("api_key", ""), base_url=cfg.get("base_url", ""), id=preset
            )
            routes[preset] = ProviderLLM(provider, web=web)
    roles = {split(m)[0] for m in (settings.model_chat, settings.model_triage, settings.model_deep)}
    if set(routes) == {"anthropic"} and roles == {"anthropic"}:
        return routes["anthropic"]
    return RoutedLLM(routes)


def build_messengers(settings: Settings) -> dict[str, Messenger]:
    messengers: dict[str, Messenger] = {}
    if settings.telegram_bot_token:
        from superaide.channels.telegram import TelegramMessenger

        messengers["telegram"] = TelegramMessenger(settings.telegram_bot_token)
    if settings.slack_bot_token:
        from superaide.channels.slack import SlackMessenger

        messengers["slack"] = SlackMessenger(settings.slack_bot_token)
    if not messengers:
        messengers["telegram"] = FakeMessenger()
        messengers["slack"] = FakeMessenger()
    messengers["web"] = WebMessenger()  # the in-app chat always works
    return messengers


def build_agent(settings: Settings | None = None) -> Agent:
    settings = settings or get_settings()
    return Agent(llm=build_llm(settings), messengers=build_messengers(settings), settings=settings)
