"""One conversation across channels: the owner's DMs merge, nothing else ever does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.brain.turn import build_messages
from superaide.db.models import Context, Event
from superaide.db.session import session_scope
from superaide.memory.store import TIMELINE_CHARS, owner_timeline, strip_label
from tests.conftest import COLLEAGUE_SLACK, MANAGER_SLACK, MANAGER_TG, sl, tg


@pytest.fixture
def chats(agent, manager, colleague, model):
    """The owner (manager) on Telegram, then in their Slack DM; Priya in hers; a group."""
    model.reply = "Noted."
    agent.handle(tg(MANAGER_TG, "btw i dont like black colour at all!"))
    agent.handle(sl(COLLEAGUE_SLACK, "Shashank owes me the Q3 numbers"))
    agent.handle(tg(MANAGER_TG, "my secret group plan", chat_kind="group", chat_id="-100500"))
    agent.handle(sl(MANAGER_SLACK, "public channel chatter", chat_kind="group", channel="C_GENERAL"))
    return manager, colleague


def _owner_dm(s, provider: str) -> Context:
    return s.scalar(select(Context).where(Context.provider == provider, Context.kind == "dm",
                                          Context.external_id.in_([MANAGER_TG, f"D_{MANAGER_SLACK}"])))  # fmt: skip


def test_the_owners_telegram_dm_reaches_their_slack_dm_labelled_and_in_order(agent, chats, model):
    model.systems.clear()
    agent.handle(sl(MANAGER_SLACK, "what colour do I dislike?"))
    with session_scope() as s:
        manager, _ = chats
        t = owner_timeline(s, manager.id, _owner_dm(s, "slack"))
        texts = [e.content for e in t.events]
        assert texts[:2] == ["btw i dont like black colour at all!", "Noted."]
        assert texts[-2:] == ["what colour do I dislike?", "Noted."]
        assert sorted(t.labels.values())[0].startswith("[On Telegram · ")
        turns = build_messages(t.events[:-1], "what colour do I dislike?", t.labels)
        assert turns[0]["role"] == "user" and turns[0]["content"].startswith("[On Telegram · ")
        assert turns[-1]["content"] == "what colour do I dislike?"  # this channel: no label


def test_nothing_from_groups_or_other_peoples_dms_ever_merges(agent, chats):
    agent.handle(sl(MANAGER_SLACK, "hello"))
    with session_scope() as s:
        manager, _ = chats
        prompt = str([e.content for e in owner_timeline(s, manager.id, _owner_dm(s, "slack")).events])
        for private in ("Q3 numbers", "secret group plan", "public channel chatter"):
            assert private not in prompt


def test_other_people_and_groups_get_no_timeline(agent, chats, model):
    seen: list[str] = []

    def recording(purpose, system, messages, output_model):
        seen.append(str(messages) + "\n".join(b.text for b in system))
        return model(purpose, system, messages, output_model)

    agent.llm.responder = recording
    agent.handle(sl(COLLEAGUE_SLACK, "what does Shashank dislike?"))
    agent.handle(tg(MANAGER_TG, "what colour do I dislike?", chat_kind="group", chat_id="-100500"))
    assert len(seen) == 2
    for prompt in seen:
        assert "black colour" not in prompt and "On Telegram" not in prompt


def test_the_window_and_the_budget_keep_it_small(agent, chats):
    manager, _ = chats
    with session_scope() as s:
        tg_dm = _owner_dm(s, "telegram")
        old = s.scalar(select(Event).where(Event.context_id == tg_dm.id, Event.direction == "in"))
        old.created_at = datetime.now(UTC) - timedelta(hours=49)
    agent.handle(sl(MANAGER_SLACK, "hi"))
    with session_scope() as s:
        texts = [e.content for e in owner_timeline(s, manager.id, _owner_dm(s, "slack")).events]
        assert "btw i dont like black colour at all!" not in texts and "Noted." in texts
    for i in range(12):
        agent.handle(tg(MANAGER_TG, f"note {i}: " + "x" * 900))
    agent.handle(sl(MANAGER_SLACK, "and now?"))
    with session_scope() as s:
        t = owner_timeline(s, manager.id, _owner_dm(s, "slack"))
        turns = build_messages(t.events, "and now?", t.labels)
        assert sum(len(m["content"]) for m in turns) < TIMELINE_CHARS + 1000
        assert all(len(m["content"]) < 2000 for m in turns)  # long turns from elsewhere are cut
        assert turns[-1]["content"].endswith("and now?")


def test_a_label_ava_copies_into_her_reply_is_removed():
    assert strip_label("[On Telegram · Wed 19:13] You said you dislike black.") == "You said you dislike black."
    assert strip_label("[In the app · Thu 09:00] Hi") == "Hi"
    assert strip_label("You said [On Telegram] earlier") == "You said [On Telegram] earlier"
