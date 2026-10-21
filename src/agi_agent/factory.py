"""Wire settings into a ready Agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agi_agent.brain.budget import BudgetGuard
from agi_agent.brain.llm import LLM, ClaudeLLM, FakeLLM, SystemBlock
from agi_agent.brain.turn import Prediction, RequestedAction, TurnOutput
from agi_agent.channels.base import FakeMessenger, Messenger, WebMessenger
from agi_agent.config import Settings, get_settings
from agi_agent.pipeline import Agent


def demo_responder(
    purpose: str, system: list[SystemBlock], messages: list[dict[str, Any]], output_model: type[BaseModel] | None
) -> Any:
    """Offline stand-in used when no Claude API key is set. Costs nothing."""
    last = messages[-1]["content"].splitlines()[-1] if messages else ""
    return TurnOutput(
        reply=(
            f'(Offline mode: no Claude API key yet, so I can only echo you.) You said: "{last}". '
            "Connect Claude under Connections to get real replies."
        ),
        requested_action=RequestedAction(kind="none", summary=""),
        prediction=Prediction(expectation="you will reply", confidence=0.5),
    )


def uses_real_model(settings: Settings) -> bool:
    """Real Claude whenever a key is present, unless FAKE_LLM=true forces offline mode."""
    return bool(settings.anthropic_api_key) and not settings.fake_llm


def build_llm(settings: Settings) -> LLM:
    if not uses_real_model(settings):
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
    messengers["web"] = WebMessenger()  # the in-app chat always works
    return messengers


def build_agent(settings: Settings | None = None) -> Agent:
    settings = settings or get_settings()
    return Agent(llm=build_llm(settings), messengers=build_messengers(settings), settings=settings)
