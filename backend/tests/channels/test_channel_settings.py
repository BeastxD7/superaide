# ruff: noqa: F811
"""#433: Slack thread depth, cross-channel context and how long other people's messages are kept are owner settings."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

from superaide.brain.turn import build_messages
from superaide.channels import settings as channel_settings
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from tests.channels.test_slack_owner_channels import CHANNEL, OWNER_SLACK, _mention, owner_linked, rec  # noqa: F401
from tests.channels.test_slack_thread_history import THREAD
from tests.integrations.gmail_fake import owner_client_fixture  # noqa: F401

URL = "/app/channels/read-settings"


def test_defaults_are_todays_behaviour(owner_client):
    with session_scope() as s:
        assert channel_settings.load(s) == {
            "slack_thread_depth": 25,
            "cross_channel_chars": 600,
            "third_party_retention_days": 0,
        }
    out = owner_client.get(URL).json()
    assert set(out["choices"]) == set(channel_settings.CHOICES)


def test_the_settings_save_refuse_bad_values_and_are_audited(owner_client):
    assert owner_client.put(URL, json={"slack_thread_depth": 7}).status_code == 422
    assert owner_client.put(URL, json={"nonsense": 1}).status_code == 422
    assert owner_client.put(URL, json={"slack_thread_depth": 10, "cross_channel_chars": 300}).status_code == 200
    with session_scope() as s:
        assert channel_settings.load(s)["slack_thread_depth"] == 10
        rows = [a for a in s.scalars(select(AuditLog)).all() if a.action == "channels_read_settings"]
    assert rows and "slack_thread_depth" in str(rows[-1].detail)


def event(text, at):
    return SimpleNamespace(id=text[:5], direction="in", content=text, created_at=at, meta={}, resolved_person_id=None)


def test_a_turn_from_another_channel_is_cut_at_the_owners_size():
    long = "x" * 2000
    at = datetime.now(UTC)
    ev = SimpleNamespace(id="a", direction="in", content=long, created_at=at, meta={})
    labels = {"a": "[On Telegram]"}
    default = build_messages([ev], "hello", labels)[0]["content"]
    short = build_messages([ev], "hello", labels, label_chars=300)[0]["content"]
    long_ = build_messages([ev], "hello", labels, label_chars=1200)[0]["content"]
    assert len(short) < len(default) < len(long_)
    assert "x" * 599 + "…" in default and "x" * 600 not in default


def test_the_slack_thread_is_read_to_the_owners_depth_and_the_cut_is_explained(
    owner_client, agent, owner_linked, rec, outbox
):
    owner_client.put(URL, json={"slack_thread_depth": 10})
    slack = outbox["slack"]
    asked = []

    def history(channel, thread_ts, limit=25):
        asked.append(limit)
        return (THREAD * 4)[:limit]  # a thread at least as long as the limit

    slack.thread_history = history
    msg = _mention(OWNER_SLACK, "<@UAVA> can you summarise this thread?")
    msg.thread_id = "1700000500.000100"
    agent.handle(msg)
    assert asked == [10]
    prompt = str(rec.calls[-1]["messages"])
    assert "Only the latest 10 messages of the thread were read" in prompt
    assert "Connections → Reading and keeping" in prompt


def test_retention_takes_the_shorter_of_the_owner_and_the_operator(owner_client):
    with session_scope() as s:
        assert channel_settings.retention_days(s, 0) == 0  # neither set: forever, as before
        assert channel_settings.retention_days(s, 180) == 180  # the operator's alone
    owner_client.put(URL, json={"third_party_retention_days": 90})
    with session_scope() as s:
        assert channel_settings.retention_days(s, 0) == 90
        assert channel_settings.retention_days(s, 30) == 30  # the operator is stricter
        assert channel_settings.retention_days(s, 365) == 90  # the owner is stricter
