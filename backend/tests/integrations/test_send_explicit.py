# ruff: noqa: F811
"""#394: the owner says "say thanks to marlin" / "send it": the draft and the approval are made in ONE step, even to
an automated address (with a strong warning on the card), and Ava never contradicts what she can do."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.approvals import recipients
from superaide.brain import capabilities
from superaide.integrations.gmail import client as api
from superaide.platform.db.models import Approval
from superaide.platform.db.session import session_scope
from tests.integrations.gmail_fake import connect, gmail_fake_fixture, mail, owner_client_fixture  # noqa: F401
from tests.pipeline.test_loop import Script, tool_use, turn_for

MARLIN = "Marlin Bank <noreply@mailer.marlin.example>"
YES = {"reaches_a_person": "no", "evidence": "bulk marketing sender"}


def _connect() -> None:
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)


def _marketing(gmail_fake):
    gmail_fake.add(
        mail("k1", thread="tk", sender=MARLIN, subject="Your offer", body="Do not reply to this automated message.")
    )


def test_the_exact_conversation_say_thanks_to_marlin_makes_the_draft_and_the_card_in_one_step(owner_client, gmail_fake):
    _marketing(gmail_fake)
    _connect()
    with session_scope() as s:
        script = Script(
            tool_use("gmail_search", {"query": "marlin"}, "a"),
            tool_use("gmail_send", {"thread_id": "tk", "text": "Thank you!", **YES}, "b"),
            "I've drafted a thank-you to Marlin and queued it for your approval. It is flagged as an automated one.",
        )
        # the owner's own words name the sender, so this is their request, not the model's idea
        result = turn_for(s, script, "Say thanks to marlin").run([{"role": "user", "content": "x"}])
        [a] = s.scalars(select(Approval)).all()
        assert a.kind == "gmail_send" and a.status == "pending" and a.payload["to"] == ["noreply@mailer.marlin.example"]
        assert a.payload["reaches_a_person"] == "no"  # still recorded
    assert gmail_fake.drafts and not gmail_fake.sent
    assert "queued it for your approval" in result.text  # backed by the tool result, so kept
    [card] = owner_client.get("/app/approvals").json()
    assert card["warnings"][0] == recipients.AUTOMATED_CHIP
    assert [f["label"] for f in card["fields"]] == ["To", "Subject"] and "Thank you!" in card["summary"]
    done = owner_client.post(f"/app/approvals/{card['id']}/approve").json()
    assert done["result"] == "Sent from your Gmail." and gmail_fake.sent == ["d1"]


def test_when_the_model_picks_an_automated_recipient_on_its_own_it_is_still_refused(owner_client, gmail_fake):
    _marketing(gmail_fake)
    _connect()
    with session_scope() as s:
        script = Script(tool_use("gmail_send", {"thread_id": "tk", "text": "Thanks", **YES}), "ok")
        turn_for(s, script, "tidy my inbox").run([{"role": "user", "content": "x"}])
        assert s.scalars(select(Approval)).all() == []
        assert "NO approval was created" in script.seen[1][-1]["content"][0]["content"]
    assert not gmail_fake.drafts


@pytest.mark.parametrize(
    "said",
    [
        "I can't send messages to other people.",
        "I cannot send emails to anyone yet.",
        "Gmail is read-only here.",
        "I'm unable to send email to others.",
    ],
)
def test_denying_a_capability_she_has_is_replaced_with_a_correction(said):
    assert capabilities.contradicts_capability(said, can_send=True)
    assert not capabilities.contradicts_capability(said, can_send=False)
    text, removed = capabilities.strip_claims(f"Sure. {said} Anything else?", set(), can_send=True)
    assert said not in text and "I can email people for you" in text and removed
    assert "Sure." in text and "Anything else?" in text


def test_the_block_says_she_can_email_in_one_step_when_drafts_are_on_and_not_when_they_are_off(
    owner_client, gmail_fake
):
    from superaide.platform.db.session import session_scope as scope

    connect(scopes=(api.READONLY,), drafts=False)
    with scope() as s:
        off = capabilities.gmail_line(s, "UTC")
    assert "read-only" in off and "cannot save drafts" in off and "CAN email" not in off
    connect(scopes=(api.READONLY, api.COMPOSE), drafts=True)
    with scope() as s:
        on = capabilities.gmail_line(s, "UTC")
    assert "you CAN email people" in on and "ONE step" in on and "Never say you can't send" in on
    assert "read-only" in on and "Gmail is read-only" in on  # only inside the "never say" instruction


def test_the_intro_greets_once_even_when_the_brain_step_came_first():
    # #394: "Hi X, I'm Ava, your personal assistant" was said by the brain step and again by the hello step.
    from superaide.app.onboarding import lines_for
    from superaide.platform.db.models import Person

    p = Person(display_name="Ananya Menon", relation="manager")
    brain = lines_for("brain", p, "Ava")
    hello_after = lines_for("hello", p, "Ava", introduced=True)
    hello_first = lines_for("hello", p, "Ava")
    assert brain[0].startswith("Hi Ananya, I'm Ava")
    assert (
        not hello_after[0].startswith("Hi")
        and "I'm Ava" not in hello_after[0]
        and "Thanks for hiring me" in hello_after[0]
    )
    assert hello_first[0].startswith("Hi Ananya, I'm Ava")  # no brain step: the hello step is the greeting
