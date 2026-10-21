"""The scenario evaluation harness, offline: every scenario file loads and runs without a harness
error, and the checks themselves catch what they should. Free and fast, so it runs in CI.

This grades the harness, not Ava: the scorecard (python -m superaide.evals run) is where product
failures are reported, so a known product bug never turns CI red here.
"""

from __future__ import annotations

import pytest

from superaide.evals import checks
from superaide.evals.harness import run_scenario, start_time
from superaide.evals.scenario import CATEGORIES, Scenario, load_all

SCENARIOS = load_all()
BY_ID = {s.id: s for s in SCENARIOS}


def test_the_suite_covers_every_category_with_about_fifty_scenarios():
    assert len(SCENARIOS) >= 50
    assert {s.category for s in SCENARIOS} == set(CATEGORIES)


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s.id)
def test_every_scenario_runs_offline_without_a_harness_error(sc: Scenario):
    run = run_scenario(sc)
    assert not run.error, run.error
    assert len(run.turns) == sum(1 for t in sc.turns if t.say or t.digest)
    assert run.cost == 0


# Scenarios whose checks are all graded offline (no model, or a scripted one) and pass today.
SELF_TESTS = [
    "stranger-gets-fixed-reply",
    "stranger-claims-to-be-owner",
    "group-never-gets-dm-content",
    "appr-message-colleague",
    "brief-morning-lists-today",
    "rem-tomorrow-fast-path",
    "rem-after-midnight-fast-path",
]


@pytest.mark.parametrize("scenario_id", SELF_TESTS)
def test_code_path_scenarios_pass_offline(scenario_id: str):
    run = run_scenario(BY_ID[scenario_id])
    assert run.graded > 0
    assert run.passed, [o for o in run.outcomes if o.status == "fail"]


def _with(sc: Scenario, **changes) -> Scenario:
    return Scenario.model_validate({**sc.model_dump(), **changes})


def test_a_wrong_expectation_fails():
    sc = BY_ID["rem-tomorrow-fast-path"]
    wrong = _with(
        sc,
        checks=[{"reminder": {"text_any": ["expense report"], "due": {"day": 2, "at": "10:00"}}}],
    )
    run = run_scenario(wrong)
    assert not run.passed
    assert "wrong time" in next(o.reason for o in run.outcomes if o.status == "fail")


def test_a_private_fact_in_a_prompt_is_caught():
    sc = BY_ID["colleague-cannot-see-private-fact"]
    # Make the note visible to colleagues: it must then show up in the prompt and fail the check.
    world = sc.world.model_dump()
    world["memories"][0]["audience"] = "org"
    run = run_scenario(_with(sc, world=world))
    failed = [o.name for o in run.outcomes if o.status == "fail"]
    assert "prompt_excludes" in failed


def test_model_dependent_checks_are_skipped_offline_not_passed():
    run = run_scenario(BY_ID["recall-colour-vs-color"])
    assert {o.status for o in run.outcomes} == {"skip"}


def test_false_capability_claims_are_flagged():
    bad = [
        "I may not keep this beyond this chat.",
        "Sorry, I can't edit or cancel reminders.",
        "I don't have the ability to remember things once this conversation ends.",
    ]
    good = ["Saved to your notebook. Only you can see it.", "Done, cancelled your 6 pm reminder."]
    assert all(any(checks._found(p, t) for p in checks.FALSE_LIMITS) for t in bad)
    assert not any(checks._found(p, t) for p in checks.FALSE_LIMITS for t in good)


def test_the_clock_starts_at_the_next_local_time_asked_for():
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    sc = _with(BY_ID["rem-weekday-monday"], clock="10:00", weekday="friday")
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # a Wednesday
    local = start_time(sc, now).astimezone(ZoneInfo(sc.world.timezone))
    assert (local.strftime("%A"), local.hour, local.minute) == ("Friday", 10, 0)
