"""#648: the retrieval golden set and its gate.

`evals/retrieval/golden.yaml` seeds Zeeshan's and Meera's messages, a colleague's DM, notes and a file, and asks
the questions of the 2026-11-20 thread. The gate fails when a query that passes in `evals/retrieval-baseline.json`
stops passing, when a category metric falls below its recorded value, or when anything private is returned to a
colleague, a stranger or a group. The tests below prove the gate really trips (a reversed ranking, a leaked gate) and
that the metrics are computed correctly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from superaide.evals import retrieval
from superaide.evals.retrieval import Query, Result, When


@pytest.fixture(scope="module")
def results() -> list[Result]:
    return retrieval.run()


def test_the_golden_set_is_valid_and_covers_every_category(results: list[Result]):
    golden = retrieval.load()
    assert {q.category for q in retrieval.expand(golden)} == set(retrieval.CATEGORIES)
    assert len(results) == len(retrieval.expand(golden))
    # every owner query other than the stop-word ones is asked again as a colleague, a stranger and in a group
    owner = [q for q in golden.queries if q.as_ == "owner" and q.category not in ("privacy_negative", "stopword")]
    assert len(results) == len(golden.queries) + 3 * len(owner)


def test_the_results_match_the_recorded_baseline(results: list[Result]):
    regressions, improvements = retrieval.compare(results, retrieval.load_baseline())
    assert not regressions, regressions
    assert not improvements, improvements  # a gap that now passes: record it with `gate --suite retrieval --update`


def test_privacy_negative_is_one_hundred_percent_today(results: list[Result]):
    row = retrieval.metrics(results)["privacy_negative"]
    assert row["absent_ok"] == 1.0
    assert retrieval.load_baseline()["metrics"]["privacy_negative"]["absent_ok"] == 1.0


def test_the_report_prints_the_four_metrics_per_category(results: list[Result]):
    text = retrieval.render(results)
    for header in ("recall@1", "recall@5", "MRR", "nDCG@5"):
        assert header in text
    for category in retrieval.CATEGORIES:
        assert f"| {category} |" in text


def test_the_gate_fails_when_the_ranking_is_reversed(monkeypatch: pytest.MonkeyPatch):
    real = retrieval.search

    def reversed_search(world, q):
        r = real(world, q)
        return Result(r.query, list(reversed(r.ranked)), r.tainted)

    monkeypatch.setattr(retrieval, "search", reversed_search)
    regressions, _ = retrieval.compare(retrieval.run(), retrieval.load_baseline())
    assert any("below the baseline" in r or "passed in the baseline, fails now" in r for r in regressions), regressions


def test_the_gate_fails_when_the_owner_gate_leaks_visitor_messages(monkeypatch: pytest.MonkeyPatch):
    from sqlalchemy import true

    from superaide.memory.recall import retrieval as recall_module

    monkeypatch.setattr(recall_module, "_owner_gate", lambda session, aud: true())
    results = retrieval.run()
    regressions, _ = retrieval.compare(results, retrieval.load_baseline())
    assert any("privacy-negative" in r for r in regressions), regressions
    assert retrieval.metrics(results)["privacy_negative"]["absent_ok"] < 1.0


def test_the_gate_fails_when_a_query_is_missing_from_the_baseline(results: list[Result]):
    baseline = retrieval.load_baseline()
    baseline["queries"].pop("time-yesterday")
    regressions, _ = retrieval.compare(results, baseline)
    assert any("time-yesterday: not in the baseline" in r for r in regressions)


# --- the metrics, on hand-made rankings ---------------------------------------------------------------------


def _result(ranked: list[str], expect: list[str], absent: list[str] | None = None, tainted: bool = False) -> Result:
    q = Query.model_validate(
        {"id": "x", "category": "time", "say": "x", "expect": expect, "absent": absent or [], "no_taint": True}
    )
    return Result(q, ranked, tainted)


def test_recall_mrr_and_ndcg_on_known_rankings():
    first = _result(["a", "b", "c"], ["a"])
    assert (first.recall_at(1), first.recall_at(5), first.mrr, first.ndcg) == (1.0, 1.0, 1.0, 1.0)
    third = _result(["x", "y", "a"], ["a"])
    assert (third.recall_at(1), third.recall_at(5), third.mrr) == (0.0, 1.0, pytest.approx(1 / 3))
    assert third.ndcg == pytest.approx(0.5)  # 1 / log2(4) over the ideal 1
    half = _result(["a", "x"], ["a", "b"])
    assert half.recall_at(5) == 0.5 and not half.passed
    missing = _result(["x"], ["a"])
    assert (missing.recall_at(5), missing.mrr, missing.ndcg) == (0.0, 0.0, 0.0)


def test_an_absent_source_or_a_taint_fails_a_query():
    assert _result(["a", "bad"], ["a"], absent=["bad"]).absent_ok is False
    assert _result(["a"], ["a"], tainted=True).absent_ok is False  # no_taint
    assert _result([], [], absent=["bad"]).passed  # nothing expected, nothing returned


# --- the clock-relative seeds ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset", range(14))
def test_last_week_is_the_previous_calendar_week_whatever_today_is(offset: int):
    tz = "Asia/Kolkata"
    now = datetime(2026, 11, 2, 9, 0, tzinfo=ZoneInfo(tz)).astimezone(UTC) + timedelta(days=offset)
    today = now.astimezone(ZoneInfo(tz)).date()
    mon_this = today - timedelta(days=today.weekday())
    for i, name in enumerate(retrieval.WEEKDAYS):
        day = When(last_week=name).at(now, tz).astimezone(ZoneInfo(tz)).date()
        assert day == mon_this - timedelta(days=7) + timedelta(days=i)
    assert When(local_day=-1).at(now, tz).astimezone(ZoneInfo(tz)).date() == today - timedelta(days=1)
    assert When(age_days=26).at(now, tz) == now - timedelta(days=26)
