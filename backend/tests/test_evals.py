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
    assert len(run.turns) == sum(
        1 for t in sc.turns if t.say or t.digest or t.press or t.share or (t.advance and t.expect)
    )
    assert run.cost == 0


# Scenarios whose checks are all graded offline (no model, or a scripted one) and pass today.
SELF_TESTS = [
    "stranger-gets-fixed-reply",
    "stranger-claims-to-be-owner",
    "group-never-gets-dm-content",
    "owner-in-channel-recognised-but-audience-limited",
    "appr-message-colleague",
    "brief-morning-lists-today",
    "rem-tomorrow-fast-path",
    "rem-after-midnight-fast-path",
    "hard-inj-web-page-exfiltrate",  # a canned web page through the loop's own web_fetch: tainted
    "hard-dup-redelivered-message",  # a redelivered platform message id is dropped, no model call
    # Triage (ADR 0010): rules and the ladder, graded offline. Known gaps until TR5, so not scored.
    "triage-vip-outage-interrupts-on-telegram",
    "triage-quiet-hours-defer-without-vip-break",
    "triage-bank-detail-change-is-verify",
    "triage-unacked-climbs-then-expires",
    "triage-ack-stops-the-ladder",
    "triage-owner-and-stranger-untouched",
    "triage-ack-by-a-colleague-is-refused",
    "triage-budget-exhausted-holds",
    "triage-fake-quote-is-discarded",
    "triage-granted-intent-needs-approval-at-infant",
    # Receptionist mode (ADR 0010 TR5): the privacy and hand-off guarantees that are graded in code.
    "rcpt-private-fact-never-reaches-the-prompt",
    "rcpt-social-engineering-gets-nothing",
    "rcpt-urgent-topic-is-an-unverified-request",
    "rcpt-payment-request-is-unverified",
    "rcpt-switched-off-gives-the-fixed-reply",
    "rcpt-group-stays-unchanged",
    "rcpt-per-sender-limit",
    # Task protocol (#330): read, plan, then ask; no plan or an unusable route means no approval.
    "proto-bluehost-reads-plans-then-asks",
    "proto-no-plan-no-approval",
    "proto-cannot-execute-route-hands-over",
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


def test_the_two_owner_isolation_check_would_catch_a_leak():
    # The same scripted turn, asked by owner A (whose data it is), must surface A's notes and
    # reminders: so when B asks and they don't appear, that's isolation, not a search that finds nothing.
    sc = BY_ID["tenancy-two-owners-isolated"]
    turns = [t.model_dump() for t in sc.turns]
    turns[0]["who"] = "owner"
    turns[0]["expect"] = [{"prompt_excludes": ["Pune", "Call Anya"]}]
    run = run_scenario(_with(sc, turns=turns, checks=[]))
    assert [o.status for o in run.outcomes] == ["fail"]
    assert "Pune" in run.outcomes[0].reason and "Call Anya" in run.outcomes[0].reason


# --- the long-term-memory gate (#51) ------------------------------------------------------------


def _ltm_results() -> dict[str, bool]:
    from superaide.evals.gate import ltm_scenarios

    runs = {sc.id: run_scenario(sc) for sc in ltm_scenarios()}
    return {sid: r.passed for sid, r in runs.items() if r.graded}  # live-only scenarios can't gate


def test_ltm_scenarios_match_the_recorded_baseline():
    from superaide.evals.gate import compare, load_baseline

    regressions, improvements = compare(_ltm_results(), load_baseline())
    assert not regressions, regressions
    assert not improvements, improvements  # a gap that now passes: record it with `gate --update`


def test_ltm_gate_fails_when_the_audience_gate_is_loosened(monkeypatch: pytest.MonkeyPatch):
    """The canary scenarios exist to catch exactly this: a search that stops filtering by audience."""
    from sqlalchemy import true

    from superaide.context import recall
    from superaide.evals.gate import compare, load_baseline

    monkeypatch.setattr(recall, "memory_filter", lambda aud, now=None: true())
    regressions, _ = compare(_ltm_results(), load_baseline())
    assert any("ltm-canary-tiers-retrieval" in r for r in regressions), regressions


def test_ltm_gate_fails_when_a_correction_no_longer_retires_the_old_fact(monkeypatch: pytest.MonkeyPatch):
    from superaide.evals.gate import compare, load_baseline
    from superaide.knowledge import learn

    # Learning ignores `replaces`: the old fact stays current and is served next to the new one.
    real = learn.extract_facts
    monkeypatch.setattr(
        learn, "extract_facts", lambda *a, **k: [f.model_copy(update={"replaces": None}) for f in real(*a, **k) or []]
    )
    regressions, _ = compare(_ltm_results(), load_baseline())
    assert any("ltm-update-city" in r for r in regressions), regressions


def test_compare_flags_new_missing_failing_and_improved_scenarios():
    from superaide.evals.gate import compare

    base = {"a": "pass", "b": "known_gap", "c": "pass", "gone": "pass"}
    regressions, improvements = compare({"a": True, "b": True, "c": False, "new": True}, base)
    assert len(regressions) == 3  # c fails, new is unrecorded, gone vanished
    assert improvements == ["b: a known gap that passes now (run with --update to record it)"]


# --- end-to-end ingestion scenarios (#51) -------------------------------------------------------


def _ingest_runs():
    from superaide.evals.gate import ltm_scenarios

    return {sc.id: run_scenario(sc) for sc in ltm_scenarios() if sc.category == "ltm_ingest"}


def test_ingest_scenarios_drive_the_real_pipeline_and_pass():
    runs = _ingest_runs()
    assert len(runs) >= 5
    for sid, run in runs.items():
        assert not run.error, (sid, run.error)
        assert run.passed and run.graded, (sid, [(o.status, o.reason) for o in run.outcomes])
    shared = [t for r in runs.values() for t in r.turns if t.mode == "share"]
    assert shared and any("picked up" in t.reply for t in shared)  # the real ingest messages


def test_the_ingest_gate_fails_when_ingest_stops_linking_facts_to_the_file(monkeypatch: pytest.MonkeyPatch):
    from superaide.knowledge import ingest

    monkeypatch.setattr(ingest, "save_facts", lambda *a, **k: 0)  # the file is stored but nothing is kept
    runs = _ingest_runs()
    assert not runs["ltm-ingest-file-fact-found-in-chat"].passed
    assert not runs["ltm-ingest-link-fact-found-in-chat"].passed


def test_the_ingest_canary_fails_when_a_files_facts_leak_to_a_colleague(monkeypatch: pytest.MonkeyPatch):
    from sqlalchemy import true

    from superaide.context import recall

    monkeypatch.setattr(recall, "memory_filter", lambda aud, now=None: true())
    assert not _ingest_runs()["ltm-ingest-file-fact-never-reaches-a-colleague"].passed


def test_a_share_turn_cannot_also_say_something():
    from pydantic import ValidationError

    from superaide.evals.scenario import Turn

    with pytest.raises(ValidationError):
        Turn.model_validate({"say": "hi", "share": {"title": "a.txt", "text": "x"}})
