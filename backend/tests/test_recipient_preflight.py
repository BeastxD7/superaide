# ruff: noqa: F811
"""#327: read the history first, warn before asking, never ask while hedging.

The Bluehost shape: the thread says the sender address is not monitored; Ava proposed gmail_send to
noreply@account.bluehost.com anyway and the owner approved it into a mailbox nobody reads."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.connectors import gmail as api
from superaide.db.models import Approval
from superaide.db.session import session_scope
from superaide.policy import recipients
from tests.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401
from tests.test_loop import Script, tool_use, turn_for

BLUEHOST = "Bluehost <noreply@account.bluehost.com>"
YES = {"reaches_a_person": "yes", "evidence": "the thread is from a support team"}


def _connect() -> None:
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)


@pytest.mark.parametrize(
    "addr",
    [
        "noreply@account.bluehost.com", "no-reply@x.com", "donotreply@x.com", "do-not-reply@x.com",
        "mailer-daemon@x.com", "postmaster@x.com", "bounce@x.com", "bounces+abc@x.com",
        "notifications@vendor.com", "alerts@vendor.com", "NoReply@X.com",
    ],
)  # fmt: skip
def test_automated_addresses_are_recognised(addr):
    assert recipients.automated_address(addr)


@pytest.mark.parametrize(
    "addr", ["jane@bluehost.com", "support@bluehost.com", "alerts@gmail.com", "bounce.house@x.com"]
)
def test_people_and_shared_human_inboxes_are_not_blocked(addr):
    assert not recipients.automated_address(addr)


def test_thread_saying_not_monitored_is_refused_when_the_model_picked_the_recipient(owner_client, gmail_fake):
    gmail_fake.add(
        mail("b1", thread="tb", sender=BLUEHOST, subject="Your invoice",
             body="Thanks for your payment. This mailbox is not monitored; please do not reply.\n"
                  "To cancel use our contact page.")
    )  # fmt: skip
    _connect()
    with session_scope() as s:
        script = Script(
            tool_use("gmail_send", {"thread_id": "tb", "text": "Please cancel.", **YES}),
            "I can't email that address: it isn't monitored. Use their contact page, or I can draft a note.",
        )
        result = turn_for(s, script, "cancel my hosting").run([{"role": "user", "content": "x"}])
        assert s.scalars(select(Approval)).all() == []
        refused = script.seen[1][-1]["content"][0]
        assert refused["is_error"] is True and "NO approval was created" in refused["content"]
        assert "not monitored" in refused["content"] and "looks automated" in refused["content"]
    assert not gmail_fake.drafts  # no draft either
    assert not recipients.has_hedge(result.text)


def test_the_same_thread_is_queued_with_a_strong_warning_when_the_owner_named_the_sender(owner_client, gmail_fake):
    # #394: "tell bluehost to cancel" is the owner's own request: one step, a card, a warning chip.
    gmail_fake.add(
        mail("b1", thread="tb", sender=BLUEHOST, subject="Your invoice", body="This mailbox is not monitored.")
    )
    _connect()
    with session_scope() as s:
        script = Script(tool_use("gmail_send", {"thread_id": "tb", "text": "Please cancel.", **YES}), "It is waiting.")
        turn_for(s, script, "tell bluehost to cancel").run([{"role": "user", "content": "x"}])
        [a] = s.scalars(select(Approval)).all()
        assert a.kind == "gmail_send" and a.payload["to"] == ["noreply@account.bluehost.com"]
    [card] = owner_client.get("/app/approvals").json()
    assert card["warnings"][0] == "This address looks automated; a reply is probably not read."
    assert [f["label"] for f in card["fields"]] == ["To", "Subject"]
    assert gmail_fake.drafts  # saved in the same step


def test_rejecting_the_card_deletes_the_draft(owner_client, gmail_fake):
    gmail_fake.add(mail("b1", thread="tb", sender=BLUEHOST, subject="Your invoice", body="Do not reply."))
    _connect()
    with session_scope() as s:
        script = Script(tool_use("gmail_send", {"thread_id": "tb", "text": "Thanks!", **YES}), "ok")
        turn_for(s, script, "say thanks to bluehost").run([{"role": "user", "content": "x"}])
        [a] = s.scalars(select(Approval)).all()
        aid = a.id
    assert gmail_fake.drafts
    assert owner_client.post(f"/app/approvals/{aid}/reject").status_code == 200
    assert not gmail_fake.drafts and sorted(gmail_fake.deleted) == [
        "d1",
        "d2",
    ]  # the signed and the plain variant (#135)


def test_unknown_or_missing_reaches_a_person_is_refused_even_for_a_human_address(owner_client, gmail_fake):
    gmail_fake.add(mail("h1", thread="th", sender="Jane Park <jane@bluehost.com>"))
    _connect()
    for args in ({"reaches_a_person": "unknown", "evidence": "?"}, {"reaches_a_person": "no"}, {}):
        with session_scope() as s:
            script = Script(tool_use("gmail_send", {"thread_id": "th", "text": "Hi", **args}), "ok")
            turn_for(s, script).run([{"role": "user", "content": "x"}])
            assert s.scalars(select(Approval)).all() == []
            assert script.seen[1][-1]["content"][0]["is_error"] is True


def test_a_human_reply_to_makes_a_card_with_to_and_subject_first(owner_client, gmail_fake):
    gmail_fake.add(
        mail("h1", thread="th", sender="Bluehost Support <support@bluehost.com>", subject="Your ticket",
             extra={"Reply-To": "Jane Park <jane@bluehost.com>"})
    )  # fmt: skip
    _connect()
    with session_scope() as s:
        script = Script(tool_use("gmail_send", {"thread_id": "th", "text": "Please cancel.", **YES}), "It's waiting.")
        turn_for(s, script).run([{"role": "user", "content": "x"}])
        [a] = s.scalars(select(Approval)).all()
        assert a.payload["to"] == ["support@bluehost.com"]  # never switched to the Reply-To
        assert a.payload["summary"].startswith("To: ") and "Subject: Re: Your ticket" in a.payload["summary"]
    [card] = owner_client.get("/app/approvals").json()
    assert [f["label"] for f in card["fields"]] == ["To", "Subject"]
    assert card["fields"][0]["value"] == "support@bluehost.com" and card["fields"][1]["value"] == "Re: Your ticket"
    assert "New recipient: not seen before in your mail." in card["warnings"]
    assert any("jane@bluehost.com" in w for w in card["warnings"])


def test_no_new_recipient_chip_for_an_address_seen_in_another_thread(owner_client, gmail_fake):
    gmail_fake.add(mail("old", thread="t-old", sender="Daniel <daniel@acme.com>"))
    gmail_fake.add(mail("m1", thread="t1", sender="Daniel <daniel@acme.com>"))
    _connect()
    with session_scope() as s:
        script = Script(tool_use("gmail_send", {"thread_id": "t1", "text": "Yes.", **YES}), "ok")
        turn_for(s, script).run([{"role": "user", "content": "x"}])
    [card] = owner_client.get("/app/approvals").json()
    assert card["warnings"] == []


def test_an_automated_sender_with_a_human_address_in_the_thread_offers_it_but_never_switches(owner_client, gmail_fake):
    gmail_fake.add(
        mail("b1", thread="tb", sender=BLUEHOST, body="Do not reply. Questions? Write to billing-help@bluehost.com")
    )
    _connect()
    with session_scope() as s:
        script = Script(tool_use("gmail_send", {"thread_id": "tb", "text": "Cancel", **YES}), "ok")
        turn_for(s, script).run([{"role": "user", "content": "x"}])
        assert s.scalars(select(Approval)).all() == []
        assert "billing-help@bluehost.com" in script.seen[1][-1]["content"][0]["content"]
        assert "do NOT switch yourself" in script.seen[1][-1]["content"][0]["content"]


def test_propose_action_for_a_message_needs_a_person_first(owner_client):
    with session_scope() as s:
        script = Script(
            tool_use("propose_action", {"kind": "message_someone_else", "summary": "Tell Priya", "rationale": "r"}),
            "ok",
        )
        result = turn_for(s, script).run([{"role": "user", "content": "x"}])
        assert s.scalars(select(Approval)).all() == []
    assert "action:message_someone_else:not_a_person" in result.notes


@pytest.mark.parametrize("hedge", recipients.HEDGES)
def test_a_reply_with_a_hedge_about_reaching_a_person_never_comes_with_an_approval(owner_client, gmail_fake, hedge):
    gmail_fake.add(mail("m1", thread="t1", sender="Daniel <daniel@acme.com>"))
    _connect()
    with session_scope() as s:
        script = Script(
            tool_use("gmail_send", {"thread_id": "t1", "text": "Yes.", **YES}),
            f"Queued for your approval, but it {hedge} so check first.",
        )
        result = turn_for(s, script).run([{"role": "user", "content": "x"}])
        [a] = s.scalars(select(Approval)).all()
        assert a.status == "rejected" and a.decided_by == "Ava (safety check)"
    assert not recipients.has_hedge(result.text) and "haven't asked for your approval" in result.text
    assert "approval_withdrawn:hedge" in result.notes
