# ci-module: approvals
"""#642: Approve and Cancel buttons under a card's notice (Telegram, and Slack when switched on). A press is checked
like a typed word and then some: the presser must be the owner's own confirmed account, in the DM the card was asked
for in; a replayed press is one press; a forwarded copy or another user decides nothing."""

# ruff: noqa: F811
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from superaide.approvals import buttons
from superaide.approvals.models import Approval
from superaide.channels.base import InboundAction
from superaide.channels.slack import parse_block_action
from superaide.platform.db.session import session_scope
from superaide.proactive import notify
from tests.conftest import sl
from tests.reception.test_inbox import OWNER, Owner, model, write  # noqa: F401
from tests.reception.test_reception import (  # noqa: F401
    ava,
    connected,
    outbox,
    owner_world,
    script,
)

DRAFT = "The owner is busy and will reply in an hour."


@pytest.fixture(autouse=True)
def wired(monkeypatch, outbox) -> None:
    monkeypatch.setattr(notify, "messenger_for", lambda provider: outbox.get(provider))


def make_card(ava, script, model, monkeypatch, providers: str = "slack") -> Approval:
    monkeypatch.setattr(buttons, "providers", lambda: set(providers.split(",")))
    write(ava, script, "hello")
    model.tool = {"name": "relay_message", "input": {"to": "Meera", "text": DRAFT}}
    model.step = 0
    ava.handle(sl(OWNER, "tell Meera I'm busy"))
    with session_scope() as s:
        a = s.scalars(select(Approval)).one()
        s.expunge(a)
        return a


def press(card: Approval, verdict: str = "y", **change: Any) -> str:
    fields: dict[str, Any] = {
        "provider": "slack", "sender_id": OWNER, "action_id": "trig-1", "data": f"ap1:{card.id}:{verdict}",
        "chat_id": f"D_{OWNER}", "message_id": "1700.1", "endpoint": "", "verified": True, "tenant_scope": "T_OURS",
    }  # fmt: skip
    fields.update(change)
    return buttons.press(InboundAction(**fields))


def status() -> str:
    with session_scope() as s:
        return s.scalars(select(Approval)).one().status


def sent(outbox) -> list[str]:
    return [m["text"] for m in outbox["slack"].sent if m.get("to_context") and "this is Ava" in m["text"]]


def test_the_notice_comes_with_approve_and_cancel_buttons(owner_world, ava, script, model, outbox, monkeypatch):
    card = make_card(ava, script, model, monkeypatch)
    [posted] = [m for m in outbox["slack"].sent if m.get("buttons")]
    assert [b[0] for b in posted["buttons"]] == ["Approve", "Cancel"]
    assert posted["buttons"][0][1] == f"ap1:{card.id}:y" and len(posted["buttons"][0][1]) <= 64
    assert posted["to_chat"] == f"D_{OWNER}"


def test_no_buttons_where_they_are_not_switched_on_or_for_the_app(owner_world, ava, script, model, outbox, monkeypatch):
    make_card(ava, script, model, monkeypatch, providers="telegram")
    assert not [m for m in outbox["slack"].sent if m.get("buttons")]


def test_a_card_that_may_not_be_approved_by_chat_gets_no_buttons(owner_world, ava, script, model, outbox, monkeypatch):
    monkeypatch.setattr(buttons, "why_not_by_text", lambda approval, trust: "it was asked for on a call")
    make_card(ava, script, model, monkeypatch)
    assert not [m for m in outbox["slack"].sent if m.get("buttons")]


def test_the_owners_approve_press_sends_once_and_a_replay_is_one_press(
    owner_world, ava, script, model, outbox, monkeypatch
):
    card = make_card(ava, script, model, monkeypatch)
    assert press(card).startswith("Sent to Meera on Slack at ")
    assert status() == "approved" and sent(outbox) == [card.payload["text"]]
    assert press(card) == "Already handled."
    assert len(sent(outbox)) == 1


def test_cancel_rejects(owner_world, ava, script, model, outbox, monkeypatch):
    card = make_card(ava, script, model, monkeypatch)
    assert press(card, "n").startswith("Cancelled")
    assert status() == "rejected" and sent(outbox) == []


@pytest.mark.parametrize(
    "change",
    [
        {"sender_id": "U_SOMEONE_ELSE"},  # another user pressed it
        {"chat_id": "C_TEAM"},  # a copy in a channel
        {"chat_id": "D_U_ELSEWHERE"},
        {"provider": "telegram", "sender_id": "9001", "tenant_scope": ""},  # the owner's Telegram, a Slack card
        {"verified": False},
        {"tenant_scope": "T_OTHER"},  # the same user id in another workspace
        {"data": "ap1:not-an-id:y"},
        {"data": "e1:abcdefgh:a"},
    ],
)
def test_redteam_these_presses_decide_nothing(owner_world, ava, script, model, outbox, monkeypatch, change):
    card = make_card(ava, script, model, monkeypatch)
    press(card, **change)
    assert status() == "pending" and sent(outbox) == []


def test_redteam_a_card_from_a_turn_that_read_other_peoples_words_is_not_approved_by_a_press(
    owner_world, ava, script, model, outbox, monkeypatch
):
    card = make_card(ava, script, model, monkeypatch)
    with session_scope() as s:
        a = s.get(Approval, card.id)
        a.payload = {**a.payload, "tainted": True}
        from superaide.approvals.binding import seal

        seal(a)
    assert "read other people's words" in press(card)
    assert status() == "pending" and sent(outbox) == []


def test_the_block_action_parses_like_a_press():
    body = {
        "type": "block_actions", "api_app_id": "A1", "team": {"id": "T_OURS"},
        "user": {"id": "U1", "team_id": "T_OURS"},
        "trigger_id": "t-9", "channel": {"id": "D1"}, "message": {"ts": "17.2"},
        "actions": [{"action_id": "ap1:x:y", "value": "ap1:x:y"}],
    }  # fmt: skip
    press_ = parse_block_action(body, verified=True)
    assert press_ is not None and (press_.sender_id, press_.chat_id, press_.message_id) == ("U1", "D1", "17.2")
    assert press_.tenant_scope == "T_OURS" and press_.endpoint == "T_OURS/A1" and press_.action_id == "t-9"
    assert parse_block_action({"type": "view_submission"}, verified=True) is None
    assert parse_block_action({**body, "actions": []}, verified=True) is None


def test_on_press_answers_and_edits_the_message(owner_world, ava, script, model, outbox, monkeypatch):
    card = make_card(ava, script, model, monkeypatch)
    action = InboundAction(
        provider="slack", sender_id=OWNER, action_id="trig-2", data=f"ap1:{card.id}:y", chat_id=f"D_{OWNER}",
        message_id="1700.1", verified=True, tenant_scope="T_OURS",
    )  # fmt: skip
    buttons.on_press("xoxb-test", action)
    assert outbox["slack"].edits[-1]["ref"] == f"D_{OWNER}:1700.1"
    assert outbox["slack"].edits[-1]["text"].startswith("Sent to Meera")


def test_telegram_buttons_are_on_by_default_and_the_escalation_handler_hands_them_over(
    owner_world, ava, script, model, outbox
):
    from superaide.channels.base import InboundMessage
    from superaide.triage import buttons as triage_buttons

    write(ava, script, "hello")
    model.tool = {"name": "relay_message", "input": {"to": "Meera", "text": DRAFT}}
    model.step = 0
    ava.handle(
        InboundMessage(
            provider="telegram",
            tenant_scope="",
            sender_id="9001",
            chat_id="9001",
            chat_kind="dm",
            text="tell Meera I'm busy",
            verified=True,
            message_id="m1",
        )  # fmt: skip
    )
    [posted] = [m for m in outbox["telegram"].sent if m.get("buttons")]
    with session_scope() as s:
        card = s.scalars(select(Approval)).one()
        s.expunge(card)
    assert posted["buttons"][0][1] == f"ap1:{card.id}:y"
    action = InboundAction(
        provider="telegram", sender_id="9001", action_id="cb-1", data=f"ap1:{card.id}:y", chat_id="9001",
        message_id="55", verified=True,
    )  # fmt: skip
    reply = triage_buttons.on_telegram_press("123:abc", action)
    assert reply.startswith("Sent to Meera on Slack")
    assert outbox["telegram"].answers[-1]["text"] == reply and outbox["telegram"].edits[-1]["ref"] == "9001:55"
    assert status() == "approved"
    # the same press again, and a stranger's press on the same card, do nothing more
    assert triage_buttons.on_telegram_press("123:abc", action) == "Already handled."
    assert len(sent(outbox)) == 1
