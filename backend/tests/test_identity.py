from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.db.models import ChannelIdentity, IdentityLinkEvent, LinkChallenge
from superaide.db.session import session_scope
from superaide.identity.linking import detect_claimed_name, detect_code, unlink
from superaide.identity.trust import Trust
from tests.conftest import MANAGER_TG, codes_sent, sl, tg

NEW_PHONE = "2002"


def test_claim_and_code_detection():
    assert detect_claimed_name("Hi, it's Jordan, new number") == "Jordan"
    assert detect_claimed_name("this is priya tan") == "priya tan"
    assert detect_claimed_name("can you help?") is None
    assert detect_code(" 123456 ") == "123456"
    assert detect_code("1234567") is None


def test_unknown_sender_gets_stranger_mode(agent, model):
    result = agent.handle(tg("999", "hello"))
    assert result.mode == "stranger"
    assert "AI assistant" in (result.reply or "")
    assert model.systems == []  # no model call for strangers
    with session_scope() as s:
        identity = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == "999"))
        assert identity is not None and identity.person_id is None and identity.trust_level == Trust.AUTHENTICATED


def test_new_channel_links_only_with_the_code_from_a_trusted_channel(agent, manager, outbox, model):
    # A new Telegram account claims to be the manager.
    result = agent.handle(tg(NEW_PHONE, "Hey, it's Jordan on my new phone"))
    assert result.mode == "stranger"
    # The code goes by DM to the manager's existing channel, never into the new chat.
    all_sent = outbox["telegram"].sent + outbox["slack"].sent
    dms = [m for m in all_sent if "stable_key" in m]
    assert len(dms) == 1 and dms[0]["stable_key"] in (MANAGER_TG, "U_MGR")
    code = codes_sent(outbox["telegram"]) + codes_sent(outbox["slack"])
    assert len(code) == 1
    code = code[0]
    replies_in_new_chat = [m["text"] for m in all_sent if m.get("to_context") == NEW_PHONE]
    assert replies_in_new_chat and all(code not in text for text in replies_in_new_chat)

    # Typing the code in the new chat links it.
    linked = agent.handle(tg(NEW_PHONE, code))
    assert linked.mode == "stranger" and "Linked" in (linked.reply or "")
    with session_scope() as s:
        identity = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == NEW_PHONE))
        assert identity.person_id == manager.id and identity.trust_level == Trust.LINKED
        assert identity.verification["method"] == "code_via_trusted_channel"
        event = s.scalar(select(IdentityLinkEvent).where(IdentityLinkEvent.identity_id == identity.id))
        assert event.action == "link"

    # From now on the new channel is the manager.
    after = agent.handle(tg(NEW_PHONE, "draft me a post"))
    assert after.mode == "known"
    assert "Relation to you: manager" in model.systems[-1]


def test_wrong_codes_lock_the_challenge(agent, manager, outbox):
    agent.handle(tg(NEW_PHONE, "it's Jordan"))
    real = (codes_sent(outbox["slack"]) or codes_sent(outbox["telegram"]))[0]
    wrong = "000000" if real != "000000" else "111111"
    replies = [agent.handle(tg(NEW_PHONE, wrong)).reply for _ in range(5)]
    assert "Too many" in (replies[-1] or "")
    # Even the right code no longer works once locked.
    assert "Linked" not in (agent.handle(tg(NEW_PHONE, real)).reply or "")
    with session_scope() as s:
        challenge = s.scalar(select(LinkChallenge))
        assert challenge.status == "locked"
        identity = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == NEW_PHONE))
        assert identity.person_id is None


def test_expired_codes_do_not_link(agent, manager, outbox):
    agent.handle(tg(NEW_PHONE, "it's Jordan"))
    code = (codes_sent(outbox["slack"]) or codes_sent(outbox["telegram"]))[0]
    with session_scope() as s:
        challenge = s.scalar(select(LinkChallenge))
        challenge.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    result = agent.handle(tg(NEW_PHONE, code))
    assert "expired" in (result.reply or "")
    with session_scope() as s:
        assert s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == NEW_PHONE)).person_id is None


def test_challenges_are_rate_limited(agent, manager, outbox):
    for _ in range(5):
        agent.handle(tg(NEW_PHONE, "it's Jordan"))
    assert len(codes_sent(outbox["slack"]) + codes_sent(outbox["telegram"])) == 3


def test_unlink_returns_the_channel_to_stranger_mode(agent, manager):
    with session_scope() as s:
        identity = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == MANAGER_TG))
        unlink(s, identity, actor="test")
    assert agent.handle(tg(MANAGER_TG, "hi")).mode == "stranger"


def test_same_reply_whether_or_not_the_claimed_person_exists(agent, manager):
    real = agent.handle(tg("3003", "it's Jordan")).reply
    fake = agent.handle(tg("4004", "it's Nobody Real")).reply
    assert real == fake  # strangers cannot probe who the agent knows


def test_slack_users_are_keyed_by_team_and_id(agent, manager):
    # Same user ID in a different team is a different identity.
    result = agent.handle(sl("U_MGR", "hi", team="T_OTHER", external=True))
    assert result.mode == "stranger"


@pytest.mark.parametrize(
    "text",
    ["hello?", "it's Jordan, new number", "123456", "/start not-a-real-invite-code"],
)
def test_every_reply_to_a_stranger_says_it_is_an_ai(agent, text):
    """EU AI Act Art. 50: people talking to Ava are told it's an AI, whatever they send first."""
    result = agent.handle(tg("999999", text))
    assert result.mode == "stranger"
    assert "AI assistant" in (result.reply or ""), result.reply


def test_a_stranger_cannot_put_a_clickable_link_into_the_code_message_to_the_owner(agent, manager, outbox):
    evil = "[Confirm your Superaide login](https://evil.example/login) `x` *y* <b>z</b>"
    agent.handle(tg(NEW_PHONE, "it's Jordan on my new phone", display=evil))
    texts = [m["text"] for m in outbox["slack"].sent + outbox["telegram"].sent if "type this code" in m["text"]]
    assert texts, "the owner should still get the code message"
    for text in texts:
        for risky in ("](", "[", "https:", "`", "*", "<b>", "//"):
            assert risky not in text, risky
        assert "Confirm your Superaide login" in text  # the words stay, the markup does not


def test_a_very_long_display_name_is_cut_short_in_the_code_message(agent, manager, outbox):
    agent.handle(tg(NEW_PHONE, "it's Jordan", display="A" * 500))
    texts = [m["text"] for m in outbox["slack"].sent + outbox["telegram"].sent if "type this code" in m["text"]]
    assert texts and all("A" * 60 not in t for t in texts)


def test_challenges_to_one_person_are_limited_across_all_the_accounts_claiming_to_be_them(agent, manager, outbox):
    for n in range(12):  # twelve different accounts, each below the per-account limit
        agent.handle(tg(f"30{n:02d}", "it's Jordan"))
    assert len(codes_sent(outbox["slack"]) + codes_sent(outbox["telegram"])) == 6
