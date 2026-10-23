"""Typing / working indicators (TYPING_INDICATORS): Telegram "typing...", Slack status or placeholder.

They are cosmetic: they never change what is said or who may see it, a failing indicator never breaks a
reply, and every indicator is ended on success, error and cancellation."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx
import pytest

from superaide.channels import health, slack
from superaide.channels.base import FakeMessenger
from superaide.channels.slack import SlackMessenger
from superaide.channels.telegram import TelegramMessenger
from tests.conftest import COLLEAGUE_SLACK, MANAGER_SLACK, MANAGER_TG, sl, tg

TOKEN = "123456789:" + "T" * 35


class OrderedMessenger(FakeMessenger):
    """A FakeMessenger that also records when replies go out, to prove the order of start, reply, close."""

    def send_to_context(self, context, text, thread_id="", sources=None):  # type: ignore[override]
        self.working_log.append("reply")
        super().send_to_context(context, text, thread_id, sources)


# ---------------------------------------------------------------- the pipeline


def test_a_model_turn_is_wrapped_by_the_indicator_and_it_ends_after_the_reply(agent, outbox, manager, model):
    outbox["telegram"] = OrderedMessenger()
    agent.messengers = outbox
    model.reply = "Sure."
    agent.handle(tg(MANAGER_TG, "hello"))
    assert outbox["telegram"].working_log == [f"start {MANAGER_TG}", "reply", f"close {MANAGER_TG}"]


def test_the_indicator_is_off_when_the_setting_is_off_and_the_reply_is_the_same(
    agent, outbox, manager, model, settings
):
    model.reply = "Sure."
    agent.handle(tg(MANAGER_TG, "hello"))
    on_reply = outbox["telegram"].sent[-1]["text"]
    assert outbox["telegram"].working_log  # on by default
    settings.typing_indicators = False
    try:
        outbox["telegram"].working_log.clear()
        agent.handle(tg(MANAGER_TG, "hello again"))
    finally:
        settings.typing_indicators = True
    assert outbox["telegram"].working_log == []
    assert outbox["telegram"].sent[-1]["text"] == on_reply  # what is said does not depend on the indicator


def test_a_failing_turn_still_ends_the_indicator_and_still_replies(agent, outbox, manager, model):
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("model fell over")

    model.__class__.__call__, original = boom, model.__class__.__call__  # type: ignore[method-assign]
    try:
        agent.handle(tg(MANAGER_TG, "hello"))
    finally:
        model.__class__.__call__ = original  # type: ignore[method-assign]
    log = outbox["telegram"].working_log
    assert log[0] == f"start {MANAGER_TG}" and log[-1] == f"close {MANAGER_TG}"
    assert outbox["telegram"].sent  # the person still got the "something went wrong" reply


def test_cancellation_while_sending_still_ends_the_indicator(agent, outbox, manager, model):
    class Cancelled(BaseException):
        pass

    def cancel(*a: Any, **k: Any) -> None:
        raise Cancelled

    outbox["telegram"].send_to_context = cancel  # type: ignore[method-assign]
    with pytest.raises(Cancelled):
        agent.handle(tg(MANAGER_TG, "hello"))
    assert outbox["telegram"].working_log == [f"start {MANAGER_TG}", f"close {MANAGER_TG}"]


def test_an_indicator_that_cannot_start_never_blocks_the_reply(agent, outbox, manager, model):
    outbox["telegram"].working_fails = True
    result = agent.handle(tg(MANAGER_TG, "hello"))
    assert result.mode == "known" and outbox["telegram"].sent


def test_an_indicator_that_cannot_close_never_blocks_the_reply(agent, outbox, manager, model):
    class BadClose:
        def close(self) -> None:
            raise RuntimeError("close failed")

    outbox["telegram"].working = lambda chat, thread="": BadClose()  # type: ignore[method-assign]
    result = agent.handle(tg(MANAGER_TG, "hello"))
    assert result.mode == "known" and outbox["telegram"].sent


def test_a_messenger_without_indicators_works_as_before(agent, outbox, manager, model):
    class Plain:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send_dm(self, identity, text):
            return None

        def send_to_context(self, context, text, thread_id="", sources=None):
            self.sent.append(text)

    plain = Plain()
    agent.messengers = {**outbox, "telegram": plain}  # type: ignore[dict-item]
    agent.handle(tg(MANAGER_TG, "hello"))
    assert plain.sent


def test_strangers_and_unverified_messages_get_no_indicator(agent, outbox, model):
    stranger = tg("999", "hello")
    agent.handle(stranger)
    unverified = tg("999", "hello")
    unverified.verified = False
    agent.handle(unverified)
    assert outbox["telegram"].working_log == []
    assert model.systems == []


def test_a_colleague_on_slack_gets_the_indicator_in_the_thread_they_will_be_answered_in(
    agent, outbox, manager, colleague, model
):
    msg = sl(COLLEAGUE_SLACK, "<@UBOT> hi", chat_kind="group", channel="C1")
    msg.message_id = "1700000000.000100"
    agent.handle(msg)
    assert outbox["slack"].working_log == ["start C1/1700000000.000100", "close C1/1700000000.000100"]
    assert outbox["slack"].sent[-1]["thread_id"] == "1700000000.000100"


def test_the_manager_on_slack_dm_gets_the_indicator(agent, outbox, manager, model):
    agent.handle(sl(MANAGER_SLACK, "hello"))
    assert outbox["slack"].working_log == [f"start D_{MANAGER_SLACK}", f"close D_{MANAGER_SLACK}"]


# ---------------------------------------------------------------- Telegram


class ChatActions:
    def __init__(self, status: int = 200, raises: bool = False) -> None:
        self.status, self.raises = status, raises
        self.calls: list[dict[str, Any]] = []
        self.methods: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.raises:
            raise httpx.ConnectError("down", request=request)
        self.methods.append(request.url.path.rsplit("/", 1)[-1])
        self.calls.append(json.loads(request.content))
        return httpx.Response(self.status, json={"ok": self.status == 200, "result": True})


def telegram(fake: ChatActions, interval: float = 0.02) -> TelegramMessenger:
    messenger = TelegramMessenger(TOKEN, httpx.Client(transport=httpx.MockTransport(fake)))
    messenger.typing_interval = interval
    return messenger


def wait_for(predicate, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached in time")


def test_telegram_sends_typing_repeatedly_until_closed_and_then_stops():
    fake = ChatActions()
    loop = telegram(fake).working("555")
    assert loop is not None
    wait_for(lambda: len(fake.calls) >= 3)
    loop.close()
    assert not loop.running
    count = len(fake.calls)
    time.sleep(0.1)
    assert len(fake.calls) == count  # nothing is sent after the reply
    assert set(fake.methods) == {"sendChatAction"}
    assert fake.calls[0] == {"chat_id": "555", "action": "typing"}
    loop.close()  # twice is fine


def test_telegram_sends_no_typing_after_the_reply_is_posted():
    """The reply stops the loop before it goes out, so a refresh can never land after the message."""
    ctx = type("C", (), {"external_id": "555"})()
    for _ in range(10):  # the race is narrow: try it many times, with a refresh due about now
        fake = ChatActions()
        m = telegram(fake, interval=0.001)
        loop = m.working("555")
        assert loop is not None
        wait_for(lambda fake=fake: bool(fake.calls))
        m.send_to_context(ctx, "Here you go")  # type: ignore[arg-type]
        time.sleep(0.02)
        assert fake.methods[-1] == "sendMessage", fake.methods[-3:]
        assert not loop.running
        loop.close()


def test_telegram_typing_in_another_chat_is_left_alone_by_a_reply_elsewhere():
    fake = ChatActions()
    m = telegram(fake)
    loop = m.working("555")
    assert loop is not None
    m.send_to_context(type("C", (), {"external_id": "777"})(), "Not for this chat")  # type: ignore[arg-type]
    assert loop.running
    loop.close()


def test_telegram_typing_goes_to_the_forum_topic():
    fake = ChatActions()
    loop = telegram(fake).working("-100777", "42")
    assert loop is not None
    wait_for(lambda: bool(fake.calls))
    loop.close()
    assert fake.calls[0]["message_thread_id"] == 42


def test_telegram_typing_gives_up_when_refused_and_never_raises():
    fake = ChatActions(status=403)  # the person blocked the bot
    loop = telegram(fake).working("555")
    assert loop is not None
    wait_for(lambda: not loop.running)
    assert len(fake.calls) == 3
    loop.close()


def test_telegram_typing_survives_a_network_error_and_is_not_channel_health(monkeypatch):
    recorded: list[Any] = []
    monkeypatch.setattr(health, "record", lambda *a, **k: recorded.append(a))
    loop = telegram(ChatActions(raises=True)).working("555")
    assert loop is not None
    wait_for(lambda: not loop.running)  # gave up quietly
    loop.close()
    assert recorded == []


def test_telegram_typing_stops_at_its_hard_limit(monkeypatch):
    from superaide.channels import telegram as tg_module

    monkeypatch.setattr(tg_module, "TYPING_MAX_SECONDS", 0.1)
    fake = ChatActions()
    loop = telegram(fake).working("555")
    assert loop is not None
    wait_for(lambda: not loop.running)  # nobody closed it: it still ends
    loop.close()


def test_telegram_threads_do_not_outlive_the_turns():
    before = threading.active_count()
    messenger = telegram(ChatActions())
    for _ in range(5):
        loop = messenger.working("555")
        assert loop is not None
        loop.close()
    assert threading.active_count() <= before


# ---------------------------------------------------------------- Slack


class SlackClient:
    """Records Web API calls; any method can be made to fail by name."""

    def __init__(self, fail: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail or {}

    def _do(self, name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, kwargs))
        if name in self.fail:
            err = Exception(self.fail[name])
            err.response = {"error": self.fail[name]}  # type: ignore[attr-defined]
            raise err
        return {"ok": True, "ts": "1700.1", "channel": kwargs.get("channel", "")}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **kwargs: self._do(name, kwargs)

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


def messenger(client: SlackClient) -> SlackMessenger:
    return SlackMessenger("xoxb-1-abc", client)


def test_slack_uses_the_assistant_status_in_a_thread_when_available():
    client = SlackClient()
    m = messenger(client)
    handle = m.working("D1", "1700.0")
    assert handle is not None and handle.status and not handle.ts
    m.send_to_context(type("C", (), {"external_id": "D1"})(), "Hello there", "1700.0")  # type: ignore[arg-type]
    handle.close()
    assert client.names() == ["assistant_threads_setStatus", "chat_postMessage"]  # no placeholder message, no edit


def test_slack_status_is_cleared_when_the_turn_ends_without_a_reply():
    client = SlackClient()
    m = messenger(client)
    handle = m.working("D1", "1700.0")
    assert handle is not None
    handle.close()
    handle.close()
    assert client.names() == ["assistant_threads_setStatus", "assistant_threads_setStatus"]
    assert client.calls[-1][1]["status"] == ""


def test_slack_without_status_falls_back_to_a_placeholder_and_the_reply_is_a_new_message():
    client = SlackClient(fail={"assistant_threads_setStatus": "missing_scope"})
    m = messenger(client)
    handle = m.working("D1", "1700.0")
    assert handle is not None and handle.ts == "1700.1"
    assert m.status_unavailable  # not asked again
    m.send_to_context(type("C", (), {"external_id": "D1"})(), "The answer", "1700.0")  # type: ignore[arg-type]
    handle.close()
    # An edit would not notify: the reply is a new message and the placeholder is deleted after it.
    assert client.names() == ["assistant_threads_setStatus", "chat_postMessage", "chat_postMessage", "chat_delete"]
    assert "chat_update" not in client.names()
    posts = [kw for n, kw in client.calls if n == "chat_postMessage"]
    assert posts[0] == {"channel": "D1", "text": slack.WORKING_TEXT, "thread_ts": "1700.0"}
    assert "The answer" in posts[1]["text"] and posts[1]["thread_ts"] == "1700.0"
    assert client.calls[-1][1] == {"channel": "D1", "ts": "1700.1"}
    client.calls.clear()
    assert m.working("D1", "1700.0") is not None
    assert "assistant_threads_setStatus" not in client.names()  # remembered


def test_slack_dm_without_a_thread_uses_the_placeholder_directly():
    client = SlackClient()
    m = messenger(client)
    handle = m.working("D1", "")
    assert handle is not None and handle.ts
    assert client.names() == ["chat_postMessage"]
    assert "thread_ts" not in client.calls[0][1]
    handle.close()


def test_slack_placeholder_that_cannot_be_deleted_is_left_and_the_reply_is_still_delivered():
    client = SlackClient(fail={"chat_delete": "message_not_found"})
    m = messenger(client)
    handle = m.working("D1", "")
    assert handle is not None
    assert m.send_to_context(type("C", (), {"external_id": "D1"})(), "The answer", "")  # type: ignore[arg-type]
    handle.close()
    assert client.names() == ["chat_postMessage", "chat_postMessage", "chat_delete"]
    assert "The answer" in client.calls[1][1]["text"]
    assert client.names().count("chat_delete") == 1  # close() does not try again


def test_slack_placeholder_is_removed_when_the_turn_ends_with_no_reply():
    client = SlackClient()
    m = messenger(client)
    handle = m.working("D1", "")
    assert handle is not None
    handle.close()
    handle.close()
    assert client.names() == ["chat_postMessage", "chat_delete"]


def test_slack_long_reply_is_all_new_messages_and_the_placeholder_goes_after_the_first():
    client = SlackClient()
    m = messenger(client)
    handle = m.working("D1", "")
    assert handle is not None
    long = "\n\n".join(f"Paragraph {i}: " + "word " * 300 for i in range(20))
    m.send_to_context(type("C", (), {"external_id": "D1"})(), long, "")  # type: ignore[arg-type]
    handle.close()
    names = client.names()
    assert "chat_update" not in names and names.count("chat_delete") == 1
    assert names[:3] == ["chat_postMessage", "chat_postMessage", "chat_delete"] and names.count("chat_postMessage") >= 3


def test_slack_working_never_raises_when_everything_fails():
    client = SlackClient(fail={"assistant_threads_setStatus": "ratelimited", "chat_postMessage": "ratelimited"})
    assert messenger(client).working("D1", "1700.0") is None
    client2 = SlackClient(fail={"chat_postMessage": "invalid_auth"})
    assert messenger(client2).working("D1", "") is None  # DeliveryFailed is swallowed too


def test_slack_a_reply_elsewhere_does_not_take_over_the_placeholder():
    client = SlackClient()
    m = messenger(client)
    handle = m.working("D1", "")
    assert handle is not None
    m.send_to_context(type("C", (), {"external_id": "D_OTHER"})(), "Not for this chat", "")  # type: ignore[arg-type]
    assert client.names()[-1] == "chat_postMessage"  # the placeholder was not touched by a reply elsewhere
    handle.close()
    assert client.names()[-1] == "chat_delete"


def test_slack_another_messenger_never_takes_the_placeholder():
    client, other = SlackClient(), SlackClient()
    m = messenger(client)
    handle = m.working("D1", "")
    assert handle is not None
    messenger(other).send_to_context(type("C", (), {"external_id": "D1"})(), "x", "")  # type: ignore[arg-type]
    assert other.names() == ["chat_postMessage"]
    handle.close()
