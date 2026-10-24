"""Channel health: a rejected bot token is recorded, shown, never silently relied on, and heals on success.

No real Telegram or Slack: sends go through httpx.MockTransport or fake Slack clients.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.channels import health
from superaide.channels.health import DeliveryFailed
from superaide.channels.slack import SlackMessenger
from superaide.channels.telegram import TelegramMessenger
from superaide.db.models import AuditLog, ChannelIdentity, Event, Intention
from superaide.db.session import session_scope
from superaide.intentions import service as intentions
from superaide.knowledge.store import preferences_for
from superaide.policy.killswitch import get_state, set_state
from superaide.proactive import notify
from superaide.proactive.notify import notify_owner
from tests.pipeline.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401  (fixture re-export)

pytestmark = pytest.mark.usefixtures("owner_client")  # a signed-up owner who hired Ava

TOKEN = "123456789:" + "B" * 35
ASK = [{"role": "user", "content": "x"}]
NOTE = "I couldn't send this on Telegram: the bot token was rejected. Reconnect Telegram under Connections."


class FakeBot:
    """A Telegram Bot API that answers sendMessage with whatever status is set."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.sent: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.status != 200:
            return httpx.Response(
                self.status, json={"ok": False, "error_code": self.status, "description": "Unauthorized"}
            )
        self.sent.append(request.content.decode())
        return httpx.Response(200, json={"ok": True, "result": {}})


@pytest.fixture
def bot(monkeypatch) -> FakeBot:
    fake = FakeBot()

    def messenger_for(provider: str) -> Any:
        if provider != "telegram":
            return None
        return TelegramMessenger(TOKEN, httpx.Client(transport=httpx.MockTransport(fake)))

    monkeypatch.setattr(notify, "messenger_for", messenger_for)
    return fake


def link_telegram(s) -> None:
    owner = hired_owner(s)
    assert owner is not None
    s.add(ChannelIdentity(provider="telegram", stable_key="1001", person_id=owner.id, lifecycle="active"))
    s.flush()


def notes(s) -> list[str]:
    return [
        e.content
        for e in s.scalars(select(Event).order_by(Event.created_at))
        if e.meta.get("kind") == "delivery_failed"
    ]


def actions(s, name: str) -> int:
    return len(list(s.scalars(select(AuditLog).where(AuditLog.action == name))))


# --- recording -----------------------------------------------------------------------------


def test_a_rejected_token_marks_telegram_auth_failed_and_a_working_send_heals_it():
    fake = FakeBot(401)
    messenger = TelegramMessenger(TOKEN, httpx.Client(transport=httpx.MockTransport(fake)))
    with pytest.raises(DeliveryFailed) as err:
        messenger.send_dm(ChannelIdentity(stable_key="1001"), "hi")
    assert err.value.kind == health.AUTH_FAILED and TOKEN not in str(err.value)
    with session_scope() as s:
        state = health.get(s, "telegram")
        assert state["status"] == health.AUTH_FAILED and state["since"]
        assert TOKEN not in state["last_error"]
        assert actions(s, "channel_health") == 1
    fake.status = 200
    assert messenger.send_dm(ChannelIdentity(stable_key="1001"), "hi") is True
    with session_scope() as s:
        assert health.get(s, "telegram")["status"] == health.OK
        assert actions(s, "channel_health") == 2  # one audit per change, not per send


def test_a_person_who_blocked_the_bot_is_not_a_token_problem():
    messenger = TelegramMessenger(TOKEN, httpx.Client(transport=httpx.MockTransport(FakeBot(403))))
    assert messenger.send_dm(ChannelIdentity(stable_key="1001"), "hi") is False
    with session_scope() as s:
        assert health.get(s, "telegram")["status"] == health.OK


class SlackRejects:
    class Error(Exception):
        def __init__(self, code: str) -> None:
            super().__init__(f"The request to the Slack API failed. (error: {code})")
            self.response = {"ok": False, "error": code}

    def __init__(self, code: str) -> None:
        self.code = code

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802 - slack_sdk's name
        raise self.Error(self.code)


def test_slack_invalid_auth_marks_slack_auth_failed():
    from superaide.db.models import Context

    with pytest.raises(DeliveryFailed):
        SlackMessenger("xoxb-x", SlackRejects("token_revoked")).send_to_context(Context(external_id="C1"), "hi")
    with session_scope() as s:
        assert health.get(s, "slack")["status"] == health.AUTH_FAILED


def test_other_slack_errors_are_not_token_problems():
    from superaide.db.models import Context

    with pytest.raises(SlackRejects.Error):
        SlackMessenger("xoxb-x", SlackRejects("channel_not_found")).send_to_context(Context(external_id="C1"), "hi")
    with session_scope() as s:
        assert health.get(s, "slack")["status"] == health.OK


# --- delivery is never silent ---------------------------------------------------------------


def test_a_failed_telegram_delivery_leaves_one_note_in_the_app_then_is_rate_limited(bot):
    bot.status = 401
    with session_scope() as s:
        link_telegram(s)
        owner = hired_owner(s)
        assert owner is not None
        assert notify_owner(s, owner, "⏰ Reminder: Gym", {"kind": "reminder"}, channel="telegram") == ["app"]
        assert notify_owner(s, owner, "⏰ Reminder: Stretch", {"kind": "reminder"}, channel="telegram") == ["app"]
        assert notes(s) == [NOTE]  # one note, not one per message
        assert actions(s, "delivery_failed") == 2 and actions(s, "delivery_failure_noted") == 1
        assert health.rejected(s, "telegram")
        # A few hours later it says so again.
        earlier = (datetime.now(UTC) - notify.NOTE_EVERY - timedelta(minutes=1)).isoformat()
        set_state(s, "channel_failure_noted:telegram", {"at": earlier})
        notify_owner(s, owner, "⏰ Reminder: Water", {"kind": "reminder"}, channel="telegram")
        assert notes(s) == [NOTE, NOTE]


def test_the_owners_telegram_preference_counts_as_asking_for_telegram(bot):
    bot.status = 401
    with session_scope() as s:
        link_telegram(s)
        owner = hired_owner(s)
        assert owner is not None
        preferences_for(s, owner).interrupt_channel = "telegram"
        assert notify_owner(s, owner, "Your briefing", {"kind": "digest"}) == ["app"]
        assert notes(s) == [NOTE]


def test_a_delivered_message_leaves_no_note_and_heals_the_channel(bot):
    with session_scope() as s:
        link_telegram(s)
        health.set_health(s, "telegram", health.AUTH_FAILED, "Telegram rejected the bot token (401)")
        owner = hired_owner(s)
        assert owner is not None
    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        assert notify_owner(s, owner, "⏰ Reminder: Gym", channel="telegram") == ["app", "telegram"]
        assert notes(s) == [] and not health.rejected(s, "telegram")
        assert get_state(s, "channel_failure_noted:telegram") == {}


def test_an_app_only_message_never_touches_telegram(bot):
    bot.status = 401
    with session_scope() as s:
        link_telegram(s)
        owner = hired_owner(s)
        assert owner is not None
        assert notify_owner(s, owner, "hello", channel="app") == ["app"]
        assert notes(s) == [] and bot.sent == []


# --- Ava knows the truth ---------------------------------------------------------------------


def test_ava_is_told_a_rejected_channel_is_not_available_and_the_reminder_tool_refuses_it():
    with session_scope() as s:
        link_telegram(s)
        health.set_health(s, "telegram", health.AUTH_FAILED, "Telegram rejected the bot token (401)")
        script = Script(
            tool_use("create_reminder", {"task": "Gym", "when": "in 1 hour", "channel": "telegram"}), "Sorry."
        )
        turn = turn_for(s, script, "remind me on telegram")
        prompt = turn.system()[1].text
        assert "Channels linked for delivery right now: the app." in prompt
        assert (
            "Telegram is linked but its bot token was rejected" in prompt and "reconnect it under Connections" in prompt
        )
        assert "and on Telegram too" not in prompt
        turn.run(ASK)
        out = script.seen[1][-1]["content"][0]
        assert out.get("is_error") is True
        assert "bot token was rejected" in out["content"] and "Connections" in out["content"]
        assert s.scalar(select(Intention)) is None


def test_a_healthy_telegram_is_offered_as_before():
    with session_scope() as s:
        link_telegram(s)
        prompt = turn_for(s, Script("Hi.")).system()[1].text
        assert "Channels linked for delivery right now: the app, Telegram." in prompt
        assert "was rejected" not in prompt


def test_a_default_reminder_says_it_will_arrive_in_the_app_only_while_telegram_is_rejected():
    with session_scope() as s:
        link_telegram(s)
        owner = hired_owner(s)
        assert owner is not None
        preferences_for(s, owner).interrupt_channel = "telegram"
        assert intentions.describe_channel(s, owner, "default") == "in the app and on Telegram"
        health.set_health(s, "telegram", health.AUTH_FAILED, "x")
        assert intentions.describe_channel(s, owner, "default").startswith("in the app only")


def test_health_is_kept_per_agent_instance(settings):
    with session_scope() as s:
        health.set_health(s, "telegram", health.AUTH_FAILED, "x")
        from superaide.db.models import AgentState

        row = s.get(AgentState, (settings.tenant_id, settings.agent_id, "channel_health:telegram"))
        assert row is not None and row.value["status"] == health.AUTH_FAILED


def test_a_reply_on_a_rejected_channel_is_recorded_not_crashed(agent, manager):
    """The bot can't answer on Telegram, but the turn still completes and is kept, marked undelivered."""
    from tests.conftest import MANAGER_TG, tg

    agent.messengers["telegram"] = TelegramMessenger(TOKEN, httpx.Client(transport=httpx.MockTransport(FakeBot(401))))
    agent.handle(tg(MANAGER_TG, "hello"))
    with session_scope() as s:
        undelivered = [e for e in s.scalars(select(Event)) if e.meta.get("undelivered") == health.AUTH_FAILED]
        assert len(undelivered) == 1 and undelivered[0].direction == "out"
        assert actions(s, "reply_not_delivered") == 1
        assert health.rejected(s, "telegram")
