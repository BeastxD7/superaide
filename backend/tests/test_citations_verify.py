"""The source check on the owner's answers: fabricated citations are dropped, uncited claims
about the owner's data are revised or owned up to, and chit-chat is left alone."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.brain import verify
from superaide.brain.budget import BudgetExceeded, BudgetGuard
from superaide.brain.llm import ClaudeLLM
from superaide.db.models import AuditLog, SpendEntry
from superaide.db.session import session_scope
from tests import test_loop
from tests.test_loop import Script, note, tool_use, turn_for

owner_client = test_loop.owner_client  # the signed-up, hired owner, for every test here
pytestmark = pytest.mark.usefixtures("owner_client")

GYM = "Your gym note from 21 Oct says you planned a session at 12pm."


def cited(text: str, uri: str, quote: str = "x") -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": text,
            "citations": [{"type": "search_result_location", "source": uri, "title": "Note", "cited_text": quote}],
        }
    ]


def checks(result) -> list[dict[str, str]]:
    return [a for a in result.activity if a.get("name") == "verify"]


def test_a_citation_of_a_source_no_tool_returned_is_dropped_and_the_claim_revised():
    with session_scope() as s:
        script = Script(
            tool_use("recall", {"query": "gym"}),
            cited("Your gym note from 21 Oct says 12pm.", "ava://memory/00000000-0000-0000-0000-000000000999"),
            "I don't have a source for a gym session; nothing in your notes mentions one.",
        )
        result = turn_for(s, script, "When's my gym session?").run([{"role": "user", "content": "When's gym?"}])
        assert s.scalar(select(AuditLog).where(AuditLog.action == "citation_verify")) is not None
    assert result.sources == [] and "[1]" not in result.text
    assert result.text == "I don't have a source for a gym session; nothing in your notes mentions one."
    assert "cite_dropped:1" in result.notes and "verify:revised" in result.notes
    ask = script.seen[-1][-1]["content"]
    assert "nothing you looked up this turn supports them" in ask and "21 Oct" in ask
    [check] = checks(result)
    assert check["state"] == "done" and "Dropped 1 citation" in check["detail"]


def test_a_real_citation_is_kept_and_passes():
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        m = note(s, person.id, "Gym on 21 Oct at 12pm")
        script = Script(
            tool_use("recall", {"query": "gym"}), cited("Gym is at 12pm on 21 Oct.", f"ava://memory/{m.id}")
        )
        result = turn_for(s, script).run([{"role": "user", "content": "When's gym?"}])
    assert result.text == "Gym is at 12pm on 21 Oct. [1]" and len(result.sources) == 1
    assert "verify:ok" in result.notes and len(script.seen) == 2
    assert checks(result)[0]["detail"] == "Every claim has a source"


def test_an_uncited_owner_claim_after_an_empty_recall_is_revised():
    with session_scope() as s:
        script = Script(
            tool_use("recall", {"query": "gym"}),
            GYM,
            "I couldn't find anything about the gym in your notes.",
        )
        result = turn_for(s, script).run([{"role": "user", "content": "Did I plan the gym?"}])
    assert result.text == "I couldn't find anything about the gym in your notes."
    assert "unsupported:1" in result.notes and "verify:revised" in result.notes
    assert len(script.seen) == 3


def test_without_recall_a_made_up_note_is_caught_and_a_stubborn_reply_gets_the_honest_line():
    with session_scope() as s:
        script = Script(GYM, GYM)  # never looks anything up, and keeps the claim when asked
        result = turn_for(s, script).run([{"role": "user", "content": "What's on today?"}])
    assert result.text.startswith(GYM) and result.text.endswith(verify.ABSTAIN)
    assert "verify:abstained" in result.notes


def test_chit_chat_and_the_owners_own_words_are_untouched():
    with session_scope() as s:
        hi = Script("Good morning! Happy to help whenever you need me.")
        result = turn_for(s, hi, "hi").run([{"role": "user", "content": "hi"}])
        assert result.text == "Good morning! Happy to help whenever you need me."
        assert len(hi.seen) == 1 and checks(result) == [] and not any(n.startswith("verify") for n in result.notes)

        echo = Script("Good luck with your flight on Friday at 7am!")
        said = [{"role": "user", "content": "my flight is Friday at 7am, wish me luck"}]
        result = turn_for(s, echo).run(said)
        assert len(echo.seen) == 1 and result.text == "Good luck with your flight on Friday at 7am!"


def test_a_reminder_confirmation_is_backed_by_the_tool_result():
    with session_scope() as s:
        script = Script(
            tool_use("create_reminder", {"task": "Call Priya", "when": "tomorrow at 5pm"}),
            "Done, I'll remind you to call Priya tomorrow at 5pm. You're all set for 5pm.",
        )
        result = turn_for(s, script, "remind me").run([{"role": "user", "content": "remind me to call Priya"}])
    assert len(script.seen) == 2 and "verify:abstained" not in result.notes
    assert result.text.endswith("You're all set for 5pm.")


def test_the_spend_cap_refusing_the_revision_still_sends_an_honest_answer():
    def responder(purpose, system, messages, output_model):
        if purpose == "verify":
            raise BudgetExceeded("cap")
        return GYM

    with session_scope() as s:
        turn = turn_for(s, Script())
        turn.llm.responder = responder
        result = turn.run([{"role": "user", "content": "x"}])
    assert result.text.endswith(verify.ABSTAIN) and "verify:abstained" in result.notes


class Block(SimpleNamespace):
    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return dict(vars(self))


class StepClient:
    """Stands in for anthropic.Anthropic in the tool loop: scripted text, fixed usage."""

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        usage = SimpleNamespace(
            input_tokens=3000,
            output_tokens=60,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            cache_creation=None,
            server_tool_use=None,
        )
        return SimpleNamespace(
            usage=usage, content=[Block(type="text", text=self.texts.pop(0))], stop_reason="end_turn"
        )


def test_the_revision_goes_through_the_budget_guard_and_costs_a_fraction_of_a_cent():
    with session_scope() as s:
        turn = turn_for(s, Script())
        turn.llm = ClaudeLLM(BudgetGuard(10), client=StepClient(GYM, "I don't have a source for a gym session."))
        result = turn.run([{"role": "user", "content": "What's on today?"}])
    assert result.text == "I don't have a source for a gym session." and "verify:revised" in result.notes
    with session_scope() as s:
        entries = {e.purpose: e for e in s.scalars(select(SpendEntry))}
    check = entries["verify"]
    assert check.status == "settled" and check.model == "claude-haiku-5-5"
    assert check.cost_usd <= check.reserved_usd < Decimal("0.05")
    assert check.cost_usd < Decimal("0.01")


def test_the_heuristic_on_its_own():
    grounding = verify.grounding_text([{"role": "user", "content": "lunch with Priya at 1pm"}], [])
    assert verify.unsupported("Your lunch with Priya is at 1pm.", grounding=grounding, recall_found=False) == []
    assert verify.unsupported("Your note from 21 Oct says gym.", grounding=grounding, recall_found=False)
    assert verify.unsupported("You said you'd skip it.", grounding=grounding, recall_found=False)
    assert verify.unsupported("You just said you'd skip it.", grounding=grounding, recall_found=False) == []
    # Quoting the owner's words from this turn back is grounded (the offline echo does this);
    # a quote of something they never wrote is not.
    assert verify.unsupported('You said: "lunch with Priya".', grounding=grounding, recall_found=False) == []
    assert verify.unsupported('You said: "dinner with Sam".', grounding=grounding, recall_found=False)
    assert verify.unsupported("It's Thursday, 14:30 here.", grounding=grounding, recall_found=False) == []
    assert verify.unsupported("Your note says gym. [1]", grounding=grounding, recall_found=False) == []
    assert verify.unsupported("Your note from 21 Oct says gym.", grounding=grounding, recall_found=True) == []
    tool = verify.grounding_text([{"role": "user", "content": [{"type": "tool_result", "content": "at 5:00 pm"}]}], [])
    assert verify.unsupported("You're all set for 5pm.", grounding=tool, recall_found=False) == []
    assert verify.unsupported("You're all set for 6pm.", grounding=tool, recall_found=False)
    assert verify.sentences("Friday at 7am. [1] Priya prefers mornings. [2] [1]") == [
        "Friday at 7am. [1]",
        "Priya prefers mornings. [2] [1]",
    ]


def test_a_draft_cut_off_inside_a_tool_call_is_not_sent_back_for_revision():
    # Live API: a tool_use with no tool_result after it is a 400, so abstain instead.
    with session_scope() as s:
        script = Script(
            {
                "content": [{"type": "text", "text": GYM}, *tool_use("recall", {"query": "gy"})],
                "stop_reason": "max_tokens",
            }
        )
        result = turn_for(s, script).run([{"role": "user", "content": "x"}])
    assert len(script.seen) == 1 and result.text.endswith(verify.ABSTAIN) and "verify:abstained" in result.notes


def test_a_recap_of_what_the_owner_said_on_another_channel_is_not_flagged():
    # Live eval (ADR 0008 WP1 scenario): "what did I just tell you on Telegram?" was answered from the
    # labelled timeline, then the source check flagged it and Ava said she had no source.
    from superaide.brain import verify

    grounding = verify.flat(
        "[On Telegram · Thu 10:00] heading to the airport now, flight is at 6, landing in Delhi around 8:30\n"
        "what did I just tell you on Telegram?"
    )
    recap = "On Telegram you told me you were heading to the airport for your Delhi flight."
    assert verify.unsupported(recap, grounding=grounding, recall_found=False) == []
    # Made-up notes are still flagged, however they're phrased.
    for made_up in ("You told me your gym note says to train legs.", "Your notes say your sister lives in Pune."):
        assert verify.unsupported(made_up, grounding=grounding, recall_found=False) == [made_up]
