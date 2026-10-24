# ruff: noqa: F811
"""#328: capability answers come from code (state plus where to change it), and action claims need a tool result."""

from __future__ import annotations

import pytest

from superaide.brain import capabilities
from superaide.connectors import gmail as api
from superaide.db.session import session_scope
from tests.connectors.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401
from tests.pipeline.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401


def system_text(s, web=False) -> str:
    turn = turn_for(s, Script("x"), web=web)
    return "\n".join(b.text for b in turn.system())


def test_gmail_not_added_says_where_to_add_it(owner_client):
    with session_scope() as s:
        text = system_text(s)
    assert "Gmail: not added. The owner can add it under Capabilities → Gmail." in text
    assert "Web search: off" in text and "Reminders: always available" in text


def test_gmail_added_but_not_connected_says_how_to_connect(owner_client, gmail_fake):
    connect(scopes=(), install=True)
    with session_scope() as s:
        text = system_text(s)
    assert "Gmail: not connected" in text and "Capabilities → Gmail → Connect" in text


def test_read_only_gmail_says_what_is_missing_and_where_to_enable_it(owner_client, gmail_fake):
    connect(scopes=(api.READONLY,), drafts=False)
    with session_scope() as s:
        text = system_text(s)
    assert "Gmail: connected, since " in text
    assert "read-only" in text and "cannot save drafts" in text and "turn on drafts" in text


def test_connected_gmail_with_drafts_lists_exactly_what_she_can_do(owner_client, gmail_fake):
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    with session_scope() as s:
        text = system_text(s)
    assert (
        "drafts on" in text
        and "reply in a thread (gmail_send)" in text
        and "gmail_compose" in text
        and "cannot forward" in text
    )
    assert "since " in text  # when it was connected, so a change since earlier in the chat is visible


def test_the_prompt_tells_her_to_say_connected_now_not_i_was_wrong(owner_client):
    with session_scope() as s:
        text = system_text(s)
    assert "say plainly that it is connected or enabled now" in text
    assert "'I was wrong' only if you actually stated something false" in text
    assert "answered from the" in text  # the rule that capability questions use this block


def test_the_approval_kinds_are_listed_with_what_yes_does(owner_client):
    with session_scope() as s:
        text = system_text(s)
    assert "Send an email from your Gmail".lower() in text and "sends the saved gmail draft" in text
    assert "message someone for you" not in text  # a kind nothing can carry out is not offered


def test_web_search_state_follows_the_skill(owner_client):
    with session_scope() as s:
        assert "Web search: on." in system_text(s, web=True)


@pytest.mark.parametrize(
    "claim",
    ["I've saved a draft in your Gmail.", "I saved a draft for Daniel.", "I have created a reply draft."],
)
def test_a_draft_claim_without_a_draft_tool_is_removed(claim):
    assert capabilities.unsupported_claims(claim, set()) == ["a saved draft"]
    assert capabilities.unsupported_claims(claim, {"gmail_draft_reply"}) == []


@pytest.mark.parametrize("claim", ["I've sent it.", "I sent the email to Daniel.", "The email has been sent."])
def test_a_sent_claim_needs_a_look_in_gmail(claim):
    assert capabilities.unsupported_claims(claim, set()) == ["a sent message"]
    assert capabilities.unsupported_claims(claim, {"gmail_search"}) == []


def test_reminder_and_queue_claims_need_their_tools():
    assert capabilities.unsupported_claims("I've set a reminder for 5pm.", set())
    assert not capabilities.unsupported_claims("I've set a reminder for 5pm.", {"create_reminder"})
    assert capabilities.unsupported_claims("I've queued it for your approval.", set())
    assert not capabilities.unsupported_claims("I've queued it for your approval.", {"propose_action"})
    assert not capabilities.unsupported_claims("Daniel replied on Friday.", set())


def test_an_unsupported_claim_is_removed_from_the_reply_in_a_turn(owner_client):
    with session_scope() as s:
        script = Script("I've saved a draft in your Gmail. Want me to change the tone?")
        result = turn_for(s, script, "reply to Daniel").run([{"role": "user", "content": "x"}])
    assert "saved a draft" not in result.text and "Want me to change the tone?" in result.text
    assert "Correction: I haven't actually done that, no tool ran for a saved draft" in result.text
    assert "claim_removed:1" in result.notes


def test_a_claim_backed_by_the_tool_result_is_kept(owner_client, gmail_fake):
    gmail_fake.add(mail("m1", thread="t1"))
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    with session_scope() as s:
        script = Script(
            tool_use("gmail_draft_reply", {"thread_id": "t1", "text": "Yes."}),
            "I've saved a draft in your Gmail.",
        )
        result = turn_for(s, script, "reply yes to Daniel").run([{"role": "user", "content": "x"}])
    assert result.text == "I've saved a draft in your Gmail."


def test_where_things_stand_comes_from_the_approval_rows_not_the_transcript(owner_client):
    # #340: she said "waiting for approval" in the app, the owner approved, then asked on Telegram.
    from datetime import UTC, datetime

    from superaide.db.models import Approval

    with session_scope() as s:
        s.add(
            Approval(
                kind="gmail_send",
                status="approved",
                decided_at=datetime.now(UTC),
                payload={"summary": "To: Kaggle <team@kaggle.com> · Thanks", "result": "Sent from your Gmail."},
            )
        )
        s.add(Approval(kind="gmail_send", status="pending", payload={"summary": "To: Dan <d@acme.com> · Hi"}))
        text = system_text(s)
    assert "Where things stand" in text
    assert "APPROVED" in text and "Sent from your Gmail." in text and "WAITING for the owner's OK" in text


def test_where_things_stand_is_capped_and_absent_when_there_is_nothing(owner_client):
    from superaide.db.models import Approval

    with session_scope() as s:
        assert "Where things stand" not in system_text(s)
        for i in range(40):
            s.add(Approval(kind="gmail_send", status="pending", payload={"summary": "x" * 200 + str(i)}))
        s.flush()
        block = capabilities.standing(s, "UTC")
    assert len(block) <= capabilities.STANDING_CHARS and block.count("\n- ") <= capabilities.STANDING_ROWS
