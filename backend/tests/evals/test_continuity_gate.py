"""#648: the conversation-continuity scenarios, gated like the human-likeness suite.

`evals/scenarios/continuity.yaml` scripts the Zeeshan and Meera chat of 2026-11-20. A scenario for behaviour that is
not built yet fails today and is a `known_gap` in `evals/continuity-baseline.json`; the PR that builds it flips it.
The guard scenarios (a tainted turn cannot send, the prompt does not grow) pass today and must keep passing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from superaide.evals import checks
from superaide.evals.gate import CONTINUITY_BASELINE, compare, continuity_scenarios, load_baseline
from superaide.evals.harness import run_scenario


def _results() -> dict[str, bool]:
    runs = {sc.id: run_scenario(sc) for sc in continuity_scenarios()}
    assert not [sid for sid, r in runs.items() if r.error], {sid: r.error for sid, r in runs.items() if r.error}
    return {sid: r.passed for sid, r in runs.items() if r.graded}


def test_the_suite_replays_the_thread_and_the_prompt_budget():
    ids = {s.id for s in continuity_scenarios()}
    assert {
        "continuity-inbox-id-in-next-prompt", "continuity-slack-thread-under-ava", "continuity-send-one-clean-card",
        "continuity-send-two-cards-asks-which", "continuity-tainted-turn-cannot-send-by-text",
        "continuity-no-email-correction-on-a-chat-relay", "continuity-small-talk-prompt-is-minimal",
        "continuity-yes-please-reads-the-one-offered-conversation", "continuity-a-promised-lookup-is-not-left-standing",
    } <= ids  # fmt: skip


def test_continuity_scenarios_match_the_recorded_baseline():
    regressions, improvements = compare(_results(), load_baseline(CONTINUITY_BASELINE))
    assert not regressions, regressions
    assert not improvements, improvements  # a gap that now passes: record it with `gate --suite continuity --update`


def test_the_gate_fails_when_a_tainted_turn_can_create_a_card(monkeypatch: pytest.MonkeyPatch):
    from superaide.brain import loop
    from superaide.reception.api import relay_request

    def unguarded(self, args):  # the taint check removed
        return relay_request(
            self.s, self.sender.person, self.sender, to=str(args.get("to") or ""), text=str(args.get("text") or ""),
            typed=self._typed_by_owner(),
        )  # fmt: skip

    monkeypatch.setattr(loop.OwnerTurn, "_relay", unguarded)
    regressions, _ = compare(_results(), load_baseline(CONTINUITY_BASELINE))
    assert any("continuity-tainted-turn-cannot-send-by-text" in r for r in regressions), regressions


def test_the_gate_fails_when_the_prompt_grows(monkeypatch: pytest.MonkeyPatch):
    from superaide.brain import loop

    real = loop.OWNER_RULES
    monkeypatch.setattr(loop, "OWNER_RULES", real + "\nAdd more words here. " * 600)
    regressions, _ = compare(_results(), load_baseline(CONTINUITY_BASELINE))
    assert any("continuity-owner-prompt-does-not-grow" in r for r in regressions), regressions


# --- the new checks, on hand-made turns -------------------------------------------------------------------------


def _call(prompt: str) -> SimpleNamespace:
    return SimpleNamespace(prompt=prompt)


def _turn(index: int, *prompts: str) -> SimpleNamespace:
    return SimpleNamespace(index=index, calls=[_call(p) for p in prompts])


def _ctx(turns: list[SimpleNamespace], current: SimpleNamespace) -> checks.Ctx:
    return checks.Ctx(run=SimpleNamespace(turns=turns), turn=current, live=False)


def test_prior_ids_in_prompt_needs_the_id_a_tool_showed_earlier():
    shown = _turn(1, "tool result: - id 3649daf7 · Zeeshan")
    kept = _turn(2, "history ... 3649daf7 ...")
    lost = _turn(2, "history: [Tools used for this reply: Checking who is waiting for you]")
    assert checks.prior_ids_in_prompt(_ctx([shown, kept], kept), True)[0]
    assert not checks.prior_ids_in_prompt(_ctx([shown, lost], lost), True)[0]
    assert not checks.prior_ids_in_prompt(_ctx([lost], lost), True)[0]  # no id was ever shown: a broken scenario


def test_prompt_tokens_under_counts_the_largest_prompt():
    small, big = _turn(1, "x" * 400), _turn(1, "x" * 400, "x" * 4300)
    assert checks.prompt_tokens_under(_ctx([small], small), 200)[0]
    assert not checks.prompt_tokens_under(_ctx([big], big), 1000)[0]


def test_the_new_checks_grade_offline():
    for name in ("prior_ids_in_prompt", "prompt_tokens_under", "approvals_pending"):
        assert name in checks.KNOWN and name in checks.CODE_ONLY
