"""Wire settings into a ready Agent.

The model wiring (`brain_ready`, `build_llm`, ...) lives in `superaide.brain.wiring`; this module only adds the
messengers and the `Agent` itself, so only the composition roots (runtime, cli) depend on `pipeline` through it.
"""

from __future__ import annotations

from superaide.brain import wiring
from superaide.channels.base import FakeMessenger, Messenger, WebMessenger
from superaide.config import Settings, get_settings
from superaide.pipeline import Agent


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
    settings = wiring.with_credits(settings or get_settings())  # the agent asks for the models that will run
    return Agent(llm=wiring.build_llm(settings), messengers=build_messengers(settings), settings=settings)
