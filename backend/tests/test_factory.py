"""Wiring settings into an Agent: which model, which messengers, and the offline echo.

Real clients (Anthropic, Telegram's httpx client, Slack's WebClient) are only
constructed here, never asked to send anything.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from superaide.brain.llm import ClaudeLLM, FakeLLM, SystemBlock
from superaide.brain.turn import TurnOutput
from superaide.channels.base import FakeMessenger, WebMessenger
from superaide.channels.slack import SlackMessenger
from superaide.channels.telegram import TelegramMessenger
from superaide.config import Settings, get_settings
from superaide.factory import brain_ready, brain_status, build_agent, build_llm, build_messengers, demo_responder
from superaide.pipeline import Agent
from tests.conftest import MANAGER_TG, tg


def make_settings(**overrides) -> Settings:
    return get_settings().model_copy(update=overrides)


# --- which model ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "fake_llm", "real"),
    [("sk-test", False, True), ("sk-test", True, False), ("", False, False), ("", True, False)],
    ids=["key and real mode", "key but FAKE_LLM", "no key", "no key and FAKE_LLM"],
)
def test_the_brain_is_ready_only_with_a_provider_and_fake_llm_off(key, fake_llm, real):
    settings = make_settings(anthropic_api_key=key, fake_llm=fake_llm)
    assert brain_ready(settings) is real
    assert brain_status(settings).provider == ("anthropic" if real else None)


def test_tests_run_offline():
    assert brain_ready(get_settings()) is False


@pytest.mark.parametrize(
    ("key", "fake_llm"), [("", False), ("sk-test", True), ("", True)], ids=["no key", "FAKE_LLM", "both"]
)
def test_build_llm_is_the_free_offline_echo_unless_claude_is_usable(key, fake_llm):
    llm = build_llm(make_settings(anthropic_api_key=key, fake_llm=fake_llm))
    assert isinstance(llm, FakeLLM)
    assert llm.responder is demo_responder


def test_build_llm_is_claude_behind_the_spend_cap_when_a_key_is_set():
    llm = build_llm(make_settings(anthropic_api_key="sk-test-not-real", fake_llm=False, max_spend_usd=2.5))
    assert isinstance(llm, ClaudeLLM)
    assert llm.guard.cap == Decimal("2.5")
    assert llm.client.api_key == "sk-test-not-real"


# --- which messengers -----------------------------------------------------------------


def test_without_tokens_messages_are_recorded_not_sent_and_the_web_chat_works():
    messengers = build_messengers(make_settings(telegram_bot_token="", slack_bot_token=""))
    assert set(messengers) == {"telegram", "slack", "web"}
    assert isinstance(messengers["telegram"], FakeMessenger)
    assert isinstance(messengers["slack"], FakeMessenger)
    assert messengers["telegram"] is not messengers["slack"]
    assert isinstance(messengers["web"], WebMessenger)


def test_a_telegram_token_gives_a_real_telegram_messenger():
    messengers = build_messengers(make_settings(telegram_bot_token="123:fake", slack_bot_token=""))
    assert set(messengers) == {"telegram", "web"}
    assert isinstance(messengers["telegram"], TelegramMessenger)
    assert messengers["telegram"].base == "https://api.telegram.org/bot123:fake"
    assert isinstance(messengers["web"], WebMessenger)


def test_a_slack_token_gives_a_real_slack_messenger():
    messengers = build_messengers(make_settings(telegram_bot_token="", slack_bot_token="xoxb-fake"))
    assert set(messengers) == {"slack", "web"}
    assert isinstance(messengers["slack"], SlackMessenger)
    assert messengers["slack"].client.token == "xoxb-fake"


def test_both_tokens_give_both_real_messengers_plus_the_web_chat():
    messengers = build_messengers(make_settings(telegram_bot_token="123:fake", slack_bot_token="xoxb-fake"))
    assert isinstance(messengers["telegram"], TelegramMessenger)
    assert isinstance(messengers["slack"], SlackMessenger)
    assert isinstance(messengers["web"], WebMessenger)


# --- the offline echo -----------------------------------------------------------------


def test_demo_responder_echoes_only_the_last_line_of_the_last_message():
    messages = [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "a reply"},
        {"role": "user", "content": "earlier line\nWhat's on today?"},
    ]
    out = demo_responder("chat", [SystemBlock("sys")], messages, TurnOutput)
    assert isinstance(out, TurnOutput)
    assert 'You said: "What\'s on today?"' in out.reply
    assert "earlier line" not in out.reply
    assert "Offline mode" in out.reply
    assert "AI model page" in out.reply
    assert "Connections" not in out.reply
    assert out.requested_action.kind == "none"
    assert 0 <= out.prediction.confidence <= 1


def test_demo_responder_copes_with_no_messages():
    out = demo_responder("chat", [], [], TurnOutput)
    assert 'You said: ""' in out.reply


def test_demo_responder_output_passes_through_the_fake_model_as_a_turn():
    llm = FakeLLM(demo_responder)
    result = llm.complete(
        purpose="chat",
        model="any",
        system=[],
        messages=[{"role": "user", "content": "hello Ava"}],
        max_tokens=100,
        output_model=TurnOutput,
    )
    assert isinstance(result.parsed, TurnOutput)
    assert 'You said: "hello Ava"' in result.parsed.reply
    assert TurnOutput.model_validate_json(result.text) == result.parsed


def test_demo_responder_copes_with_an_empty_last_message():
    out = demo_responder("chat", [], [{"role": "user", "content": ""}], TurnOutput)
    assert 'You said: ""' in out.reply


# --- the agent ------------------------------------------------------------------------


def test_build_agent_wires_the_model_and_messengers_from_the_given_settings():
    settings = make_settings(
        agent_name="Bea", anthropic_api_key="sk-test-not-real", fake_llm=False, telegram_bot_token="123:fake"
    )
    agent = build_agent(settings)
    assert isinstance(agent, Agent)
    assert agent.settings is settings
    assert agent.name == "Bea"
    assert isinstance(agent.llm, ClaudeLLM)
    assert set(agent.messengers) == {"telegram", "web"}
    assert isinstance(agent.messengers["telegram"], TelegramMessenger)


def test_build_agent_without_settings_uses_the_environment_and_stays_offline():
    agent = build_agent()
    assert agent.settings is get_settings()
    assert agent.name == "Ava"
    assert isinstance(agent.llm, FakeLLM)
    assert isinstance(agent.messengers["telegram"], FakeMessenger)
    assert isinstance(agent.messengers["web"], WebMessenger)


def test_offline_agent_answers_the_manager_with_the_echo(manager):
    agent = build_agent(make_settings(anthropic_api_key="", fake_llm=True, telegram_bot_token="", slack_bot_token=""))
    result = agent.handle(tg(MANAGER_TG, "Are you there?\nPing me back"))
    assert 'You said: "Ping me back"' in (result.reply or "")
    [sent] = agent.messengers["telegram"].sent
    assert 'You said: "Ping me back"' in sent["text"]
