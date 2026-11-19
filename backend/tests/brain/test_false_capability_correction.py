# ci-module: brain
"""#639: "Correction: I can email people for you" was appended to replies that were about Telegram or Slack
messages, and true sentences were removed. The correction now needs a sentence about email, a sentence that
names the owner's approval is never a denial, and a false "I can't message people" while a relay card is waiting
is replaced by what the approval record says."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

from superaide.brain import capabilities
from superaide.brain.llm import FakeLLM
from superaide.platform.db.session import session_scope
from superaide.reception.relay import pending_sentences
from tests.conftest import sl
from tests.reception.test_inbox import OWNER, model, write  # noqa: F401
from tests.reception.test_reception import ava, connected, outbox, owner_world, script  # noqa: F401
from tests.reception.test_relay import BUSY, DRAFT, relay, wired  # noqa: F401

EMAIL_CORRECTION = "Correction: I can email people for you"


@pytest.mark.parametrize(
    "said",
    [
        "I can't send messages to other people directly. However, I can help you draft the message, "
        "and you can send it yourself.",
        "I cannot send messages on Telegram for other people without your approval.",
        "I am unable to message people on Telegram directly.",
        "I can't send messages on Slack to people either.",
    ],
)
def test_sentences_that_are_not_about_email_get_no_email_correction(said):
    text, removed = capabilities.strip_claims(said, set(), can_send=True)
    assert EMAIL_CORRECTION not in text and removed == [] and text == said


def test_a_read_only_claim_is_corrected_only_when_gmail_drafts_are_on():
    said = "Your Gmail is read-only right now."
    on, removed = capabilities.strip_claims(said, set(), can_send=True)
    assert EMAIL_CORRECTION in on and removed
    off, none = capabilities.strip_claims(said, set(), can_send=False)
    assert off == said and none == []


def test_read_only_about_something_else_is_left_alone():
    said = "The shared calendar is read-only for you."
    assert capabilities.strip_claims(said, set(), can_send=True) == (said, [])


@pytest.mark.parametrize("said", ["I can't email other people.", "I cannot send emails to anyone yet."])
def test_the_394_case_still_gets_the_correction_when_drafts_are_on_and_never_when_gmail_is_not_connected(said):
    text, removed = capabilities.strip_claims(f"Sure. {said} Bye.", set(), can_send=True)
    assert EMAIL_CORRECTION in text and said not in text and removed
    assert capabilities.strip_claims(said, set(), can_send=False) == (said, [])


def test_a_denial_that_mentions_approval_is_never_replaced_even_with_a_card_waiting():
    said = "I can't send messages to other people until you approve."
    waiting = ["Your message to Meera on Slack is waiting for your OK."]
    assert capabilities.strip_claims(said, set(), can_send=True, pending=waiting) == (said, [])


def test_a_false_denial_with_a_card_waiting_is_replaced_by_the_true_state():
    waiting = ["Your message to Zeeshan on Telegram is waiting for your OK."]
    text, removed = capabilities.strip_claims(
        "I can't send messages to other people directly. I can help you draft it.",
        set(),
        can_send=True,
        pending=waiting,
    )
    assert text == "Your message to Zeeshan on Telegram is waiting for your OK.\nI can help you draft it."
    assert EMAIL_CORRECTION not in text and removed


def test_the_pending_card_sentence_comes_from_the_approval_not_the_model(owner_world, ava, script, model, outbox):
    from tests.reception.test_relay import cards

    write(ava, script, "can you ask him to call me?")
    assert "Queued for the owner's approval" in relay(ava, model, {"to": "Meera", "text": DRAFT})
    [card] = cards()
    assert card.status == "pending"
    with session_scope() as s:
        assert pending_sentences(s) == ["Your message to Meera on Slack is waiting for your OK."]
    # The model, asked to send it now, wrongly says it cannot. The reply names the real recipient and channel.
    ava.llm = FakeLLM(lambda *a: "I can't send messages to other people directly. Want me to draft it?")
    ava.handle(sl(OWNER, "yes please send the message now"))
    last = outbox["slack"].sent[-1]["text"]
    assert "Your message to Meera on Slack is waiting for your OK." in last
    assert EMAIL_CORRECTION not in last and "can't send messages to other people" not in last
