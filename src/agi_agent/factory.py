"""Wire settings into a ready Agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agi_agent.brain.budget import BudgetGuard
from agi_agent.brain.llm import LLM, ClaudeLLM, FakeLLM, SystemBlock
from agi_agent.brain.turn import Prediction, RequestedAction, TurnOutput
from agi_agent.channels.base import FakeMessenger, Messenger
from agi_agent.config import Settings, get_settings
from agi_agent.pipeline import Agent


def demo_responder(
    purpose: str, system: list[SystemBlock], messages: list[dict[str, Any]], output_model: type[BaseModel] | None
) -> Any:
    """Offline stand-in for Claude: echoes the last message. Costs nothing."""
    last = messages[-1]["content"].splitlines()[-1] if messages else ""
    return TurnOutput(
        reply=f"(offline model) I heard: {last}",
        requested_action=RequestedAction(kind="none", summary=""),
        prediction=Prediction(expectation="you will reply", confidence=0.5),
    )


def build_llm(settings: Settings) -> LLM:
    if settings.fake_llm:
        return FakeLLM(demo_responder)
    return ClaudeLLM(BudgetGuard(settings.max_spend_usd), api_key=settings.anthropic_api_key)


def build_messengers(settings: Settings) -> dict[str, Messenger]:
    messengers: dict[str, Messenger] = {}
    if settings.telegram_bot_token:
        from agi_agent.channels.telegram import TelegramMessenger

        messengers["telegram"] = TelegramMessenger(settings.telegram_bot_token)
    if settings.slack_bot_token:
        from agi_agent.channels.slack import SlackMessenger

        messengers["slack"] = SlackMessenger(settings.slack_bot_token)
    if not messengers:
        messengers["telegram"] = FakeMessenger()
        messengers["slack"] = FakeMessenger()
    return messengers


def build_agent(settings: Settings | None = None) -> Agent:
    settings = settings or get_settings()
    return Agent(llm=build_llm(settings), messengers=build_messengers(settings), settings=settings)
