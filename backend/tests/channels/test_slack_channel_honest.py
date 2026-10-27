# ruff: noqa: F811
"""#509: in a Slack channel Ava is as honest as in a DM. Gmail and Calendar are not OPENED where others can read
(that policy stays), but she says so truthfully and never claims she has no Gmail or no capabilities list.
The model is fake: these tests measure what she is told, what she is offered, and what is changed in her reply."""

from __future__ import annotations

import pytest

from superaide.brain import capabilities
from tests.channels.test_slack_owner_channels import OWNER_SLACK, _mention, owner_linked, rec  # noqa: F401
from tests.integrations.gmail_fake import connect, gmail_fake_fixture  # noqa: F401

ASK = "<@UAVA> what's up? anything that needs my attention, any daily digest for me?"
FALSE_DENIALS = [
    "No. I don't have access to your Gmail.",
    "I don't have an email tool, so I can't read your Gmail even if access was granted.",
    "I don't have a capabilities list in my setup.",
    "You may want to ask whoever manages your Superaide setup.",
]


def test_the_channel_prompt_lists_what_exists_and_why_it_is_not_used_here(agent, owner_linked, rec, gmail_fake):
    connect(scopes=("https://www.googleapis.com/auth/gmail.readonly",))
    agent.handle(_mention(OWNER_SLACK, ASK))
    system = rec.calls[-1]["system"]
    assert "What you can do right now in this shared conversation" in system
    assert "Gmail (connected" in system
    assert "EXIST" in system and "other people can read" in system
    assert "Never say you have no access, no email tool or no capabilities list" in system


def test_the_channel_prompt_carries_names_and_state_only_never_mail_data(agent, owner_linked, rec, gmail_fake):
    connect()
    agent.handle(_mention(OWNER_SLACK, ASK))
    prompt = rec.prompt
    assert "jordan@example.com" not in prompt  # not even the connected address
    assert "Lunch on Friday" not in prompt


@pytest.mark.parametrize("denial", FALSE_DENIALS)
def test_a_false_denial_in_a_channel_is_replaced_by_the_real_reason(agent, owner_linked, rec, gmail_fake, denial):
    connect()
    rec.reply = f"Hi Jordan. {denial} Anything else?"
    result = agent.handle(_mention(OWNER_SLACK, "<@UAVA> you don't have my gmail?"))
    reply = result.reply or ""
    assert denial not in reply
    assert capabilities.CHANNEL_POLICY in reply
    assert "Anything else?" in reply  # the rest of her answer survives


def test_the_same_sentence_in_a_dm_is_left_alone(agent, owner_linked, rec, gmail_fake):
    from tests.conftest import sl

    connect()
    rec.reply = "I don't have access to that thread."
    result = agent.handle(sl(OWNER_SLACK, "can you see the thread?"))
    assert result.reply == "I don't have access to that thread."


def test_when_gmail_really_is_not_connected_the_true_sentence_stays(agent, owner_linked, rec):
    rec.reply = "Gmail isn't connected. Add it under Capabilities → Gmail."
    result = agent.handle(_mention(OWNER_SLACK, "<@UAVA> do you have my gmail?"))
    assert result.reply == "Gmail isn't connected. Add it under Capabilities → Gmail."


def test_a_reply_that_already_gives_the_channel_reason_is_kept(agent, owner_linked, rec, gmail_fake):
    connect()
    good = "I don't open your email in a channel because others can read it; ask me in a DM."
    rec.reply = good
    reply = agent.handle(_mention(OWNER_SLACK, "<@UAVA> my gmail?")).reply or ""
    assert good in reply and capabilities.CHANNEL_POLICY not in reply  # kept, and not said twice
