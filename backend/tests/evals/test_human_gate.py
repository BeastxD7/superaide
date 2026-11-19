"""#484: the human-likeness suite. The scenarios replay the Meera transcript and others offline with the fake model;
this gate fails CI when a scenario that passes in `evals/human-baseline.json` stops passing, and the tests below prove
the gate really does catch a lost message, a silent owner and a stranger's words copied into the owner's chat."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from superaide.evals import checks
from superaide.evals.gate import HUMAN_BASELINE, compare, human_scenarios, load_baseline
from superaide.evals.harness import run_scenario


def _results() -> dict[str, bool]:
    runs = {sc.id: run_scenario(sc) for sc in human_scenarios()}
    return {sid: r.passed for sid, r in runs.items() if r.graded}


def test_the_suite_has_the_meera_replay_and_twenty_more_scenarios():
    ids = [s.id for s in human_scenarios()]
    assert "human-meera-replay" in ids and len(ids) >= 21
    meera = next(s for s in human_scenarios() if s.id == "human-meera-replay")
    assert sum(1 for t in meera.turns if t.who == "stranger") == 15


def test_human_scenarios_match_the_recorded_baseline():
    regressions, improvements = compare(_results(), load_baseline(HUMAN_BASELINE))
    assert not regressions, regressions
    assert not improvements, improvements  # a gap that now passes: record it with `gate --suite human --update`


def test_the_human_pass_rate_stays_above_its_threshold():
    from superaide.evals.gate import MIN_PASS_RATE, below_threshold

    results = _results()
    by_category = {s.id: s.category for s in human_scenarios()}
    assert not below_threshold(results, by_category), below_threshold(results, by_category)
    assert MIN_PASS_RATE["human"] >= 0.8


def test_the_threshold_trips_when_known_gaps_pile_up():
    from superaide.evals.gate import below_threshold

    by_category = {f"s{i}": "human" for i in range(10)}
    assert below_threshold({f"s{i}": i < 7 for i in range(10)}, by_category)  # 7 of 10 is under 80 percent
    assert not below_threshold({f"s{i}": i < 8 for i in range(10)}, by_category)


def test_the_gate_fails_when_a_message_is_not_stored(monkeypatch: pytest.MonkeyPatch):
    from superaide.reception import service

    monkeypatch.setattr(service, "_keep", lambda *a, **k: None)
    regressions, _ = compare(_results(), load_baseline(HUMAN_BASELINE))
    assert any("human-meera-replay" in r for r in regressions), regressions


def test_the_gate_fails_when_the_owner_is_not_told(monkeypatch: pytest.MonkeyPatch):
    from superaide.reception import service

    monkeypatch.setattr(service, "_note", lambda *a, **k: None)
    regressions, _ = compare(_results(), load_baseline(HUMAN_BASELINE))
    assert any("human-meera-replay" in r for r in regressions), regressions


def test_the_gate_fails_when_the_owners_note_quotes_the_stranger(monkeypatch: pytest.MonkeyPatch):
    from superaide.reception import service

    real = service._reason
    monkeypatch.setattr(service, "_reason", lambda s, o, row: real(s, o, row) + ": " + row.message)
    regressions, _ = compare(_results(), load_baseline(HUMAN_BASELINE))
    assert any("human-injection-in-stranger-text" in r for r in regressions), regressions


# --- the checks themselves, on hand-made turns ---------------------------------------------------------------


def _turn(index: int, said: str, reply: str, via: str = "telegram", who: str = "stranger") -> SimpleNamespace:
    return SimpleNamespace(index=index, said=said, reply=reply, via=via, who=who, chat="dm", calls=[], tools=[])


def _ctx(*turns: SimpleNamespace) -> checks.Ctx:
    return checks.Ctx(run=SimpleNamespace(turns=list(turns)), turn=None, live=False)


def test_cap_stated_needs_the_number_in_the_first_reply():
    ok = _ctx(_turn(1, "hi", "Hi. You can send up to 10 messages an hour here."))
    late = _ctx(_turn(1, "hi", "Hi."), _turn(2, "x", "You can send up to 10 messages an hour here."))
    assert checks.cap_stated(ok, True)[0] and not checks.cap_stated(late, True)[0]


def test_no_repeat_reply_catches_the_same_words_twice_in_a_row():
    twice = _ctx(_turn(1, "a", "Noted."), _turn(2, "b", "Noted."))
    other_channel = _ctx(_turn(1, "a", "Noted."), _turn(2, "b", "Noted.", via="slack"))
    silent = _ctx(_turn(1, "a", "Noted."), _turn(2, "b", ""), _turn(3, "c", "Noted."))
    assert not checks.no_repeat_reply(twice, True)[0]
    assert checks.no_repeat_reply(other_channel, True)[0]
    assert not checks.no_repeat_reply(silent, True)[0]  # silence between does not excuse the repeat


def test_replies_identical_compares_the_named_turns():
    same = _ctx(_turn(1, "a", "Same."), _turn(2, "b", "Same."))
    differ = _ctx(_turn(1, "a", "Same."), _turn(2, "b", "Same!"))
    assert checks.replies_identical(same, [1, 2])[0] and not checks.replies_identical(differ, [1, 2])[0]


@pytest.mark.parametrize(
    ("said", "reply", "ok"),
    [
        ("it is about the project", "Noted. If we work together, type it here to link this account.", False),
        ("please tell them it is important", "Ok. type it here to link this account.", False),
        ("I'm Meera", "Hi. type it here to link this account.", True),
        ("it is about the project", "Noted.", True),  # no note, nothing to object to
    ],
)
def test_no_spurious_claim_note(said: str, reply: str, ok: bool):
    assert checks.no_spurious_claim_note(_ctx(_turn(1, said, reply)), True)[0] is ok


def test_every_human_check_grades_offline():
    for name in (
        "all_inbound_stored", "no_false_passed_on", "cap_stated", "owner_told",
        "no_stranger_text_in_owner_prompt", "no_repeat_reply", "replies_identical", "no_spurious_claim_note",
    ):  # fmt: skip
        assert name in checks.KNOWN and name in checks.CODE_ONLY
