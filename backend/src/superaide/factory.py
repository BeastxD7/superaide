"""Wire settings into a ready Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from superaide.brain.budget import BudgetGuard, Limits
from superaide.brain.llm import LLM, ClaudeLLM, FakeLLM, SystemBlock
from superaide.brain.pricing import PriceRegistry, owner_prices
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput
from superaide.channels.base import FakeMessenger, Messenger, WebMessenger
from superaide.config import Settings, get_settings
from superaide.db.session import session_scope
from superaide.pipeline import Agent


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


def brain_status(settings: Settings) -> BrainStatus:
    """A real model whenever a provider is connected, unless FAKE_LLM=true forces offline mode.
    Today the only provider is Anthropic; ADR 0007 adds others behind this same check."""
    if settings.fake_llm:
        return BrainStatus(False, None, "fake_llm")
    if settings.anthropic_api_key:
        return BrainStatus(True, "anthropic", "connected")
    return BrainStatus(False, None, "no_provider")


def brain_ready(settings: Settings) -> bool:
    return brain_status(settings).ready


def load_prices() -> PriceRegistry:
    """Built-in prices plus any the owner set in the app."""
    with session_scope() as s:
        return owner_prices(s)


def build_llm(settings: Settings) -> LLM:
    if not brain_ready(settings):
        return FakeLLM(demo_responder)
    guard = BudgetGuard(settings.max_spend_usd, limits=Limits.from_settings(settings), prices=load_prices())
    return ClaudeLLM(guard, api_key=settings.anthropic_api_key)


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
