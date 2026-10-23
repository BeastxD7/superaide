"""Telegram adapter: update parsing, sending and the long-polling loop. No real network."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import httpx
import pytest

from superaide.channels import health, telegram
from superaide.channels.health import DeliveryFailed
from superaide.channels.telegram import TelegramMessenger, parse_update, poll_forever, verify_secret
from superaide.db.models import ChannelIdentity, Context
from superaide.db.session import session_scope
from superaide.pipeline import parse_start_code

TOKEN = "123456789:" + "A" * 35


def update(
    text: Any = "hello",
    *,
    update_id: int = 1,
    sender: dict[str, Any] | None = None,
    chat: dict[str, Any] | None = None,
    key: str = "message",
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 7,
        "from": sender if sender is not None else {"id": 1001, "is_bot": False, "first_name": "Jordan"},
        "chat": chat or {"id": 1001, "type": "private"},
    }
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, key: message}


# --- secret token -----------------------------------------------------------------


def test_an_empty_header_does_not_match_a_configured_secret():
    assert not verify_secret("", "test-telegram-secret")
    assert not verify_secret("anything", "")  # no secret configured: nothing verifies
    assert not verify_secret("test-telegram-secret ", "test-telegram-secret")
    assert verify_secret("test-telegram-secret", "test-telegram-secret")


# --- parsing ----------------------------------------------------------------------


def test_a_private_chat_is_a_dm_with_the_numeric_ids_as_strings():
    msg = parse_update(
        update(sender={"id": 1001, "first_name": "Jordan", "last_name": "Whitfield", "username": "jw"}), verified=True
    )
    assert msg is not None
    assert (msg.provider, msg.tenant_scope, msg.sender_id, msg.chat_id) == ("telegram", "", "1001", "1001")
    assert (msg.chat_kind, msg.message_id, msg.sender_display, msg.sender_username) == ("dm", "7", "Jordan Whitfield", "jw")
    assert msg.verified is True and msg.raw["update_id"] == 1


def test_the_verified_flag_is_passed_through_not_assumed():
    msg = parse_update(update(), verified=False)
    assert msg is not None and msg.verified is False


@pytest.mark.parametrize("kind", ["group", "supergroup", "channel"])
def test_any_non_private_chat_is_a_group_and_keeps_its_title(kind):
    msg = parse_update(update(chat={"id": -100123, "type": kind, "title": "Board"}), verified=True)
    assert msg is not None
    assert (msg.chat_kind, msg.chat_id, msg.chat_title, msg.sender_id) == ("group", "-100123", "Board", "1001")


def test_a_display_name_without_a_last_name_has_no_trailing_space():
    msg = parse_update(update(sender={"id": 5, "first_name": "Priya"}), verified=True)
    assert msg is not None and msg.sender_display == "Priya" and msg.sender_username == ""


def test_edited_messages_are_ignored():
    assert parse_update(update("changed my mind", key="edited_message"), verified=True) is None


@pytest.mark.parametrize(
    "upd",
    [
        update(None),  # sticker, photo, voice: no text
        update("   \n "),
        update(42),
        {"update_id": 3, "message": "not a dict"},
        {"update_id": 3, "callback_query": {"id": "1", "data": "x"}},
        {"update_id": 3, "my_chat_member": {"chat": {"id": 1}}},
        update(sender={}),  # channel posts have no sender
        update(sender={"id": 99, "is_bot": True, "first_name": "Other bot"}),
    ],
    ids=["no-text", "blank", "non-string", "message-not-dict", "callback", "member-update", "no-sender", "bot"],
)
def test_updates_without_a_human_text_message_are_ignored(upd):
    assert parse_update(upd, verified=True) is None


def test_a_start_deep_link_keeps_its_code_for_the_pipeline():
    msg = parse_update(update("/start Ab3_xY9-kLmN"), verified=True)
    assert msg is not None and msg.text == "/start Ab3_xY9-kLmN"
    assert parse_start_code(msg.text) == "Ab3_xY9-kLmN"


# --- sending ----------------------------------------------------------------------


def recording_client(status: int = 200, body: dict[str, Any] | None = None) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body or {"ok": status == 200})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_send_dm_posts_to_the_bot_api_with_the_user_id_as_chat_id():
    client, seen = recording_client()
    TelegramMessenger(TOKEN, client).send_dm(ChannelIdentity(stable_key="1001"), "Hi Jordan")
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert body["chat_id"] == "1001" and body["text"] == "Hi Jordan" and body["parse_mode"] == "HTML"


def test_send_to_context_posts_to_the_group_chat_id():
    client, seen = recording_client()
    TelegramMessenger(TOKEN, client).send_to_context(Context(external_id="-100123"), "Noted")
    [request] = seen
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert body["chat_id"] == "-100123" and body["text"] == "Noted" and body["parse_mode"] == "HTML"


def test_a_refused_send_is_logged_not_raised(caplog):
    client, seen = recording_client(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})
    with caplog.at_level(logging.WARNING, logger="superaide.channels.telegram"):
        TelegramMessenger(TOKEN, client).send_dm(ChannelIdentity(stable_key="1001"), "hello?")
    assert len(seen) == 1
    assert "telegram send failed: 403" in caplog.text and "blocked" in caplog.text


def test_the_token_never_appears_in_a_send_failure_log(caplog):
    client, _ = recording_client(500, {"ok": False})
    with caplog.at_level(logging.WARNING, logger="superaide.channels.telegram"), pytest.raises(DeliveryFailed) as err:
        TelegramMessenger(TOKEN, client).send_dm(ChannelIdentity(stable_key="1001"), "x")
    assert caplog.text and TOKEN not in caplog.text
    assert TOKEN not in str(err.value)
    with session_scope() as s:
        assert health.get(s, "telegram")["status"] == health.UNREACHABLE


# --- polling ----------------------------------------------------------------------


class InstantStop(threading.Event):
    """A stop event whose back-off wait returns at once, recording how long it was asked to wait."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout or 0)
        return self.is_set()


class FakeTelegram:
    """Answers getUpdates from a script; stops the loop once the script is used up."""

    def __init__(self, script: list[Any], stop: threading.Event) -> None:
        self.script = list(script)
        self.stop = stop
        self.requests: list[httpx.Request] = []
        self.offsets: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        assert request.url.path == f"/bot{TOKEN}/getUpdates"
        self.offsets.append(int(request.url.params["offset"]))
        step = self.script.pop(0) if self.script else []
        if not self.script:
            self.stop.set()
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(200, json={"ok": True, "result": step})


@pytest.fixture
def fake_telegram(monkeypatch):
    real_client = httpx.Client

    def install(script: list[Any], stop: threading.Event | None = None) -> FakeTelegram:
        fake = FakeTelegram(script, stop or InstantStop())
        monkeypatch.setattr(telegram.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(fake)))
        return fake

    return install


def test_polling_removes_the_webhook_first_then_long_polls(fake_telegram):
    fake = fake_telegram([[]])
    poll_forever(TOKEN, lambda m: None, fake.stop)
    assert [r.url.path for r in fake.requests] == [f"/bot{TOKEN}/deleteWebhook", f"/bot{TOKEN}/getUpdates"]
    assert fake.requests[0].method == "POST" and fake.requests[1].method == "GET"
    assert fake.requests[1].url.params["timeout"] == "20"


def test_polling_advances_the_offset_past_every_update_it_saw(fake_telegram):
    fake = fake_telegram(
        [
            [update("one", update_id=10), update("two", update_id=11)],
            [update(None, update_id=12)],  # ignored, but still acknowledged
            [],
        ]
    )
    got: list[str] = []
    poll_forever(TOKEN, lambda m: got.append(m.text), fake.stop)
    assert fake.offsets == [0, 12, 13]
    assert got == ["one", "two"]


def test_polled_updates_count_as_verified(fake_telegram):
    fake = fake_telegram([[update("hi")]])
    got = []
    poll_forever(TOKEN, got.append, fake.stop)
    assert [m.verified for m in got] == [True]


def test_one_failing_message_does_not_stop_the_loop(fake_telegram, caplog):
    fake = fake_telegram([[update("boom", update_id=1), update("fine", update_id=2)], [update("later", update_id=3)]])
    handled: list[str] = []

    def handle(msg):
        handled.append(msg.text)
        if msg.text == "boom":
            raise RuntimeError("pipeline blew up")

    with caplog.at_level(logging.ERROR, logger="superaide.channels.telegram"):
        poll_forever(TOKEN, handle, fake.stop)
    assert handled == ["boom", "fine", "later"]
    assert fake.offsets == [0, 3]
    assert "failed to handle telegram update" in caplog.text


def test_a_network_error_backs_off_and_polling_resumes(fake_telegram, caplog):
    stop = InstantStop()
    fake = fake_telegram([httpx.ConnectError("down"), [update("back", update_id=5)], []], stop)
    got = []
    with caplog.at_level(logging.WARNING, logger="superaide.channels.telegram"):
        poll_forever(TOKEN, lambda m: got.append(m.text), stop)
    assert got == ["back"]
    assert stop.waits == [3]
    assert fake.offsets == [0, 0, 6]
    assert "telegram poll error" in caplog.text


def test_a_garbled_response_backs_off_and_polling_resumes(fake_telegram):
    stop = InstantStop()
    fake = fake_telegram([httpx.Response(200, content=b"<html>bad gateway</html>"), [update("ok", update_id=1)]], stop)
    got = []
    poll_forever(TOKEN, lambda m: got.append(m.text), stop)
    assert got == ["ok"] and stop.waits == [3] and fake.offsets == [0, 0]


def test_a_failed_delete_webhook_does_not_prevent_polling(monkeypatch, caplog):
    stop = InstantStop()
    real_client = httpx.Client
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("/deleteWebhook"):
            raise httpx.ConnectError("no route")
        stop.set()
        return httpx.Response(200, json={"ok": True, "result": [update("hi")]})

    monkeypatch.setattr(telegram.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(handler)))
    got = []
    with caplog.at_level(logging.WARNING, logger="superaide.channels.telegram"):
        poll_forever(TOKEN, got.append, stop)
    assert paths == ["deleteWebhook", "getUpdates"] and len(got) == 1
    assert "deleteWebhook failed" in caplog.text


def test_polling_returns_at_once_when_already_stopped(fake_telegram):
    stop = InstantStop()
    stop.set()
    fake = fake_telegram([], stop)
    poll_forever(TOKEN, lambda m: None, stop)
    assert [r.url.path for r in fake.requests] == [f"/bot{TOKEN}/deleteWebhook"]


def test_an_error_response_from_telegram_backs_off_before_polling_again(fake_telegram):
    stop = InstantStop()
    conflict = httpx.Response(409, json={"ok": False, "error_code": 409, "description": "Conflict"})
    fake_telegram([conflict, conflict, []], stop)
    poll_forever(TOKEN, lambda m: None, stop)
    assert len(stop.waits) >= 2


def test_one_malformed_update_does_not_stop_the_loop(fake_telegram):
    stop = InstantStop()
    bad = update("x", update_id=1, sender={"first_name": "no id"})
    fake = fake_telegram([[bad, update("fine", update_id=2)], []], stop)
    got = []
    poll_forever(TOKEN, lambda m: got.append(m.text), stop)
    assert got == ["fine"] and fake.offsets == [0, 3]


def test_a_rejected_token_backs_off_for_minutes_and_records_the_health(fake_telegram):
    stop = InstantStop()
    revoked = httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
    fake_telegram([revoked, revoked, revoked], stop)
    poll_forever(TOKEN, lambda m: None, stop)
    assert stop.waits == [telegram.AUTH_BACKOFF] * 3 and telegram.AUTH_BACKOFF >= 300
    with session_scope() as s:
        state = health.get(s, "telegram")
        assert state["status"] == health.AUTH_FAILED and TOKEN not in state["last_error"]


def test_polling_that_works_again_marks_telegram_healthy(fake_telegram):
    stop = InstantStop()
    revoked = httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
    fake_telegram([revoked, [update("back", update_id=1)]], stop)
    got: list[str] = []
    poll_forever(TOKEN, lambda m: got.append(m.text), stop)
    assert got == ["back"]
    with session_scope() as s:
        assert health.get(s, "telegram")["status"] == health.OK


def test_a_slow_turn_does_not_block_polling_or_other_chats(fake_telegram):
    release = threading.Event()
    done: list[str] = []

    def handle(msg):
        if msg.text == "slow":
            assert release.wait(10)
        else:
            if msg.text == "quick":
                release.set()  # the slow turn can only finish once another chat was served
        done.append(msg.text)

    slow_chat = {"id": 2001, "type": "private"}
    fake = fake_telegram(
        [[update("slow", update_id=1, chat=slow_chat), update("again", update_id=2, chat=slow_chat)],
         [update("quick", update_id=3)]]
    )  # fmt: skip
    poll_forever(TOKEN, handle, fake.stop)
    assert fake.offsets == [0, 3]  # polling went on past the slow turn
    assert done.index("quick") < done.index("slow") < done.index("again")  # one chat stays in order
