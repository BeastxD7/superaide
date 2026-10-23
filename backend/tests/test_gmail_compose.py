# ruff: noqa: F811
"""#339: a brand-new email (gmail_compose): approval first, recipient typed by the owner, checked in code."""

from __future__ import annotations

from sqlalchemy import select

from superaide.brain.llm import RoutedLLM
from superaide.connectors import gmail as api
from superaide.db.models import Approval
from superaide.db.session import session_scope
from tests.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401
from tests.test_loop import Script, tool_use, turn_for

ASK = "email jane@startup.example saying I am not feeling well and will be out tomorrow"
ARGS = {
    "to": "jane@startup.example",
    "subject": "Out tomorrow",
    "text": "Hi Jane, I am not feeling well and will be out tomorrow. Thanks, Jordan",
    "reaches_a_person": "yes",
    "evidence": "the owner typed a personal address",
}


def _connect() -> None:
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)


def test_a_new_email_to_an_address_the_owner_typed_is_drafted_approved_and_sent(owner_client, gmail_fake):
    _connect()
    with session_scope() as s:
        script = Script(tool_use("gmail_compose", ARGS), "It's waiting for your OK.")
        turn_for(s, script, ASK).run([{"role": "user", "content": ASK}])
        [a] = s.scalars(select(Approval)).all()
        assert a.kind == "gmail_send" and a.status == "pending" and a.payload["to"] == ["jane@startup.example"]
        assert a.payload["subject"] == "Out tomorrow" and a.payload["compose"] is True
        approval_id = a.id
    assert gmail_fake.draft_to("d1") == "To: jane@startup.example" and not gmail_fake.sent
    [card] = owner_client.get("/app/approvals").json()
    assert [f["label"] for f in card["fields"]] == ["To", "Subject"]
    assert card["fields"][0]["value"] == "jane@startup.example" and card["executes"] is True
    assert "New recipient: not seen before in your mail." in card["warnings"]
    done = owner_client.post(f"/app/approvals/{approval_id}/approve").json()
    assert done["result"] == "Sent from your Gmail." and gmail_fake.sent == ["d1"]


def refused(owner_client, gmail_fake, args, text=ASK, read_mail=False):
    _connect()
    if read_mail:
        gmail_fake.add(mail("m1", thread="t1"))
    with session_scope() as s:
        steps = [tool_use("gmail_read_thread", {"thread_id": "t1"}, "a")] if read_mail else []
        script = Script(*steps, tool_use("gmail_compose", args, "b"), "ok")
        turn_for(s, script, text).run([{"role": "user", "content": text}])
        assert s.scalars(select(Approval)).all() == []
        out = script.seen[-1][-1]["content"][0]
        assert out["is_error"] is True and "NO approval was created" in out["content"]
        return out["content"]
    assert not gmail_fake.drafts


def test_an_address_the_owner_did_not_type_is_refused(owner_client, gmail_fake):
    msg = refused(owner_client, gmail_fake, {**ARGS, "to": "ceo@evil.example"})
    assert "not one the owner typed" in msg and not gmail_fake.drafts


def test_an_automated_address_is_refused_even_if_typed(owner_client, gmail_fake):
    text = "email noreply@account.bluehost.com that I want to cancel"
    msg = refused(owner_client, gmail_fake, {**ARGS, "to": "noreply@account.bluehost.com"}, text)
    assert "looks automated" in msg


def test_unknown_reach_or_two_addresses_or_a_turn_that_read_email_is_refused(owner_client, gmail_fake):
    assert "reaches_a_person" in refused(owner_client, gmail_fake, {**ARGS, "reaches_a_person": "unknown"})
    assert "exactly one" in refused(owner_client, gmail_fake, {**ARGS, "to": "jane@startup.example, x@y.example"})
    assert "read email in this turn" in refused(owner_client, gmail_fake, ARGS, read_mail=True)


def test_the_airlock_placeholder_recipient_round_trips_to_the_real_address(owner_client, gmail_fake):
    _connect()
    with session_scope() as s:
        script = Script(tool_use("gmail_compose", {**ARGS, "to": "[email 1]"}), "Waiting for your OK.")
        turn = turn_for(s, script, ASK)
        turn.llm = RoutedLLM({"anthropic": turn.llm})
        turn.run([{"role": "user", "content": ASK}])
        [a] = s.scalars(select(Approval)).all()
        assert a.payload["to"] == ["jane@startup.example"]
        assert "jane@startup.example" not in str(script.seen)  # the model never saw it
