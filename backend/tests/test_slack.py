"""Slack adapter: request signing, event parsing, sending and Socket Mode wiring. No real network."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest

from superaide.channels import slack
from superaide.channels.slack import (
    MAX_SKEW_SECONDS,
    SlackMessenger,
    parse_event,
    run_socket_mode,
    socket_mode_handler,
    verify_signature,
)
from superaide.db.models import ChannelIdentity, Context
from tests.conftest import COLLEAGUE_SLACK, OUR_TEAM

SECRET = "test-slack-secret"
NOW = 1_760_000_000
BODY = b'{"type":"event_callback"}'


def sign(body: bytes = BODY, ts: int = NOW, secret: str = SECRET) -> tuple[str, str]:
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return str(ts), sig


def event(
    text: Any = "hello",
    *,
    etype: str = "message",
    channel_type: str | None = "im",
    user: str | None = COLLEAGUE_SLACK,
    team: str = OUR_TEAM,
    **extra: Any,
) -> dict[str, Any]:
    ev: dict[str, Any] = {"type": etype, "channel": "D123", "ts": "1760000000.000100", **extra}
    if text is not None:
        ev["text"] = text
    if user is not None:
        ev["user"] = user
    if channel_type is not None:
        ev["channel_type"] = channel_type
    return {"type": "event_callback", "team_id": team, "event": ev}


# --- signing ----------------------------------------------------------------------


def test_a_correctly_signed_request_is_accepted():
    ts, sig = sign()
    assert verify_signature(SECRET, ts, BODY, sig, now=NOW)


def test_a_signature_made_with_another_secret_is_refused():
    ts, sig = sign(secret="someone-elses-secret")
    assert not verify_signature(SECRET, ts, BODY, sig, now=NOW)


def test_a_signature_for_another_timestamp_is_refused():
    _, sig = sign(ts=NOW)
    assert not verify_signature(SECRET, str(NOW + 1), BODY, sig, now=NOW)


@pytest.mark.parametrize("offset", [MAX_SKEW_SECONDS + 1, -(MAX_SKEW_SECONDS + 1), 3600])
def test_a_replayed_or_future_request_is_refused_even_if_correctly_signed(offset):
    ts, sig = sign(ts=NOW + offset)
    assert not verify_signature(SECRET, ts, BODY, sig, now=NOW)


def test_a_request_exactly_at_the_skew_limit_is_still_accepted():
    ts, sig = sign(ts=NOW - MAX_SKEW_SECONDS)
    assert verify_signature(SECRET, ts, BODY, sig, now=NOW)


@pytest.mark.parametrize(
    ("secret", "ts", "sig"),
    [("", str(NOW), "v0=abc"), (SECRET, None, "v0=abc"), (SECRET, str(NOW), None), (SECRET, "", "v0=abc")],
    ids=["no-secret-configured", "no-timestamp", "no-signature", "empty-timestamp"],
)
def test_missing_pieces_mean_the_request_is_refused(secret, ts, sig):
    assert not verify_signature(secret, ts, BODY, sig, now=NOW)


def test_a_non_numeric_timestamp_is_refused():
    _, sig = sign()
    assert not verify_signature(SECRET, "yesterday", BODY, sig, now=NOW)


def test_with_no_secret_even_an_unsigned_hmac_of_empty_key_is_refused():
    ts, sig = sign(secret="")
    assert not verify_signature("", ts, BODY, sig, now=NOW)


# --- parsing ----------------------------------------------------------------------


def test_a_direct_message_is_a_dm_from_our_team():
    msg = parse_event(event("hi Ava"), our_team_id=OUR_TEAM, verified=True)
    assert msg is not None
    assert (msg.provider, msg.tenant_scope, msg.sender_id, msg.chat_id) == ("slack", OUR_TEAM, COLLEAGUE_SLACK, "D123")
    assert (msg.chat_kind, msg.text, msg.message_id) == ("dm", "hi Ava", "1760000000.000100")
    assert msg.external_sender is False
    assert msg.verified is True and msg.raw["team_id"] == OUR_TEAM


def test_plain_channel_messages_are_ignored_unless_the_agent_is_mentioned():
    for kind in ("channel", "group", "mpim"):
        assert parse_event(event("chatter", channel_type=kind), our_team_id=OUR_TEAM, verified=True) is None


def test_a_mention_in_a_channel_is_a_group_message():
    payload = event("<@UAVA> can you help?", etype="app_mention", channel_type=None, channel="C42")
    msg = parse_event(payload, our_team_id=OUR_TEAM, verified=True)
    assert msg is not None and msg.chat_kind == "group" and msg.chat_id == "C42"


def test_a_sender_from_another_team_is_external_and_scoped_to_their_team():
    payload = event("hello from Acme", user_team="T_ACME")
    msg = parse_event(payload, our_team_id=OUR_TEAM, verified=True)
    assert msg is not None and msg.external_sender is True and msg.tenant_scope == "T_ACME"


def test_the_payload_team_is_used_when_the_event_has_no_user_team():
    msg = parse_event(event("hi", team="T_ACME"), our_team_id=OUR_TEAM, verified=True)
    assert msg is not None and msg.external_sender is True and msg.tenant_scope == "T_ACME"


def test_the_senders_own_team_wins_over_the_workspace_that_delivered_the_event():
    payload = event("hi", team="T_ACME", user_team=OUR_TEAM)
    msg = parse_event(payload, our_team_id=OUR_TEAM, verified=True)
    assert msg is not None and msg.external_sender is False and msg.tenant_scope == OUR_TEAM


def test_without_a_configured_team_nobody_is_marked_external():
    msg = parse_event(event("hi", user_team="T_ACME"), our_team_id="", verified=True)
    assert msg is not None and msg.external_sender is False and msg.tenant_scope == "T_ACME"


def test_the_verified_flag_is_passed_through_not_assumed():
    msg = parse_event(event(), our_team_id=OUR_TEAM, verified=False)
    assert msg is not None and msg.verified is False


@pytest.mark.parametrize(
    "payload",
    [
        event("I am a bot", bot_id="B1"),
        event("edited", subtype="message_changed"),
        event("deleted", subtype="message_deleted"),
        event("joined", subtype="channel_join"),
        event("via bot", subtype="bot_message"),
        event(None),
        event("   "),
        event(["not", "text"]),
        event("no user", user=None),
        event("x", etype="reaction_added"),
        {"type": "url_verification", "challenge": "xyz", "token": "t"},
        {"type": "event_callback"},
    ],
    ids=[
        "bot-id",
        "message_changed",
        "message_deleted",
        "channel_join",
        "bot_message",
        "no-text",
        "blank",
        "non-string",
        "no-user",
        "other-event",
        "url_verification",
        "no-event",
    ],
)
def test_events_that_are_not_a_person_writing_are_ignored(payload):
    assert parse_event(payload, our_team_id=OUR_TEAM, verified=True) is None


# --- sending ----------------------------------------------------------------------


class FakeWebClient:
    def __init__(self, token: str = "") -> None:
        self.token = token
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def conversations_open(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("conversations_open", kwargs))
        return {"ok": True, "channel": {"id": "D_OPENED"}}

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802 - slack_sdk's name
        self.calls.append(("chat_postMessage", kwargs))
        return {"ok": True}


def test_send_dm_opens_the_conversation_then_posts_into_it():
    client = FakeWebClient()
    SlackMessenger("xoxb-test", client).send_dm(ChannelIdentity(stable_key=COLLEAGUE_SLACK), "Hi Priya")
    assert client.calls == [
        ("conversations_open", {"users": COLLEAGUE_SLACK}),
        ("chat_postMessage", {"channel": "D_OPENED", "text": "Hi Priya"}),
    ]


def test_send_to_context_posts_straight_into_the_channel():
    client = FakeWebClient()
    SlackMessenger("xoxb-test", client).send_to_context(Context(external_id="C42"), "Noted")
    assert client.calls == [("chat_postMessage", {"channel": "C42", "text": "Noted"})]


def test_without_a_client_the_messenger_builds_a_web_client_with_the_bot_token(monkeypatch):
    import slack_sdk

    monkeypatch.setattr(slack_sdk, "WebClient", FakeWebClient)
    messenger = SlackMessenger("xoxb-real-looking")
    assert isinstance(messenger.client, FakeWebClient) and messenger.client.token == "xoxb-real-looking"


# --- Socket Mode ------------------------------------------------------------------


class FakeApp:
    instances: list[FakeApp] = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.listeners: dict[str, Any] = {}
        FakeApp.instances.append(self)

    def event(self, name: str):
        def register(fn):
            self.listeners[name] = fn
            return fn

        return register


class FakeSocketModeHandler:
    def __init__(self, app: FakeApp, app_token: str) -> None:
        self.app, self.app_token = app, app_token
        self.started = False

    def start(self) -> None:
        self.started = True


@pytest.fixture
def fake_bolt(monkeypatch):
    import slack_bolt
    import slack_bolt.adapter.socket_mode as socket_mode

    FakeApp.instances = []
    monkeypatch.setattr(slack_bolt, "App", FakeApp)
    monkeypatch.setattr(socket_mode, "SocketModeHandler", FakeSocketModeHandler)
    return FakeApp


def test_the_socket_mode_handler_uses_both_tokens_and_listens_for_dms_and_mentions(fake_bolt):
    handler = socket_mode_handler("xoxb-bot", "xapp-app", OUR_TEAM, lambda m: None)
    assert isinstance(handler, FakeSocketModeHandler) and handler.app_token == "xapp-app"
    assert handler.app.token == "xoxb-bot"
    assert set(handler.app.listeners) == {"message", "app_mention"}


def test_socket_mode_events_reach_the_handler_as_verified_messages(fake_bolt):
    got = []
    handler = socket_mode_handler("xoxb-bot", "xapp-app", OUR_TEAM, got.append)
    handler.app.listeners["message"](event("dm over the socket"))
    handler.app.listeners["app_mention"](event("<@UAVA> hi", etype="app_mention", channel_type=None))
    assert [(m.text, m.chat_kind, m.verified) for m in got] == [
        ("dm over the socket", "dm", True),
        ("<@UAVA> hi", "group", True),
    ]


def test_socket_mode_uses_our_team_to_spot_external_senders(fake_bolt):
    got = []
    handler = socket_mode_handler("xoxb-bot", "xapp-app", OUR_TEAM, got.append)
    handler.app.listeners["message"](event("from Acme", user_team="T_ACME"))
    assert [m.external_sender for m in got] == [True]


def test_socket_mode_drops_bot_messages_and_edits_without_calling_the_handler(fake_bolt):
    got = []
    handler = socket_mode_handler("xoxb-bot", "xapp-app", OUR_TEAM, got.append)
    handler.app.listeners["message"](event("echo", bot_id="B_AVA"))
    handler.app.listeners["message"](event("edit", subtype="message_changed"))
    handler.app.listeners["message"](event("chatter", channel_type="channel"))
    assert got == []


def test_run_socket_mode_starts_the_handler(fake_bolt, monkeypatch):
    built: list[FakeSocketModeHandler] = []
    real = slack.socket_mode_handler

    def spy(*args: Any) -> FakeSocketModeHandler:
        h = real(*args)
        built.append(h)
        return h

    monkeypatch.setattr(slack, "socket_mode_handler", spy)
    run_socket_mode("xoxb-bot", "xapp-app", OUR_TEAM, lambda m: None)
    assert len(built) == 1 and built[0].started
