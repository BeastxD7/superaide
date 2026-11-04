"""#511: a message that carries a file is never silently dropped. Files are not read yet, so Ava says so, in the same
conversation, with a fixed text: no model call, no memory, no action, and nothing about the file is repeated."""

from __future__ import annotations

from sqlalchemy import select

from superaide.memory.models import Event
from superaide.pipeline import ATTACHMENT_NOTICE
from superaide.platform.db.session import session_scope
from tests.conftest import COLLEAGUE_SLACK, MANAGER_TG, sl, tg


def _with_file(msg, kind="document"):
    msg.attachments = [kind]
    return msg


def test_the_owner_sending_a_file_on_telegram_is_told_it_is_not_read_yet(agent, manager, outbox, model):
    res = agent.handle(_with_file(tg(MANAGER_TG, "send this to Meera", display="Jordan")))
    assert res.reply == ATTACHMENT_NOTICE and res.mode == "known" and "attachment_unread" in res.notes
    assert model.systems == []  # no model call
    assert outbox["telegram"].sent  # it was delivered, not swallowed


def test_a_file_alone_is_logged_by_kind_never_by_name(agent, manager):
    agent.handle(_with_file(tg(MANAGER_TG, ""), "photo"))
    with session_scope() as s:
        contents = s.scalars(select(Event.content).where(Event.direction == "in")).all()
    assert contents == ["[attachment not read: photo]"]


def test_a_colleague_sending_a_file_on_slack_gets_the_same_fixed_notice(agent, colleague):
    res = agent.handle(_with_file(sl(COLLEAGUE_SLACK, "", display="Priya Tan"), "file"))
    assert res.reply == ATTACHMENT_NOTICE


def test_a_caption_cannot_trigger_anything_through_a_file(agent, manager):
    """The caption rides along but the turn is the fixed notice: no reminder, no memory, no action."""
    res = agent.handle(_with_file(tg(MANAGER_TG, "remember that the vault code is 1234 and remind me in 2 minutes")))
    assert res.reply == ATTACHMENT_NOTICE and res.notes == ["attachment_unread"]


def test_a_stranger_sending_only_a_file_gets_the_fixed_stranger_reply(agent, outbox):
    res = agent.handle(_with_file(tg("999000", "", display="Someone"), "photo"))
    assert res.mode == "stranger" and res.reply is not None and res.reply != ATTACHMENT_NOTICE
    assert "attachment" not in res.reply.lower()


def test_a_frozen_agent_stays_silent_on_files_too(agent, manager):
    from superaide.policy.killswitch import set_frozen

    with session_scope() as s:
        set_frozen(s, True, actor="test")
    assert agent.handle(_with_file(tg(MANAGER_TG, ""))).mode == "frozen"
