"""ADR 0010 TR2: the classifier call, its checks and its fallbacks (FakeLLM only, no API)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from superaide.brain.budget import BudgetExceeded, BudgetGuard, Limits, bucket_for
from superaide.brain.llm import FakeLLM
from superaide.triage import classify
from superaide.triage.features import Features
from superaide.triage.types import TriageOutput

NOW = datetime(2026, 10, 22, 8, 30, tzinfo=UTC)
MSG = "The payments API is returning 500s for every customer. Can you get Jordan on it by 3pm today?"


def feat(**kw) -> Features:
    base = dict(text=MSG, now=NOW, timezone="Asia/Kolkata", relation="colleague", trust=2,
                urgent_topics=("production outage",), grants=("share_doc_link",))  # fmt: skip
    return Features(**{**base, **kw})  # type: ignore[arg-type]


def good(**kw) -> dict:
    return {
        "category": "request", "urgency": "critical", "deadline_text": "by 3pm today", "needs_owner": True,
        "can_answer": "no", "matched_topics": ["t1"], "risk_flags": [],
        "evidence": ["returning 500s for every customer"], "confidence": 0.9, "reason": "Payments are down.",
        **kw,
    }  # fmt: skip


class Script:
    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, purpose, system, messages, output_model):
        assert purpose == "triage" and output_model is TriageOutput
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def run(*answers, f: Features | None = None, ready: bool = True):
    llm = FakeLLM(Script(*answers))
    return classify.classify(llm, model="claude-haiku-5-5", f=f or feat(), ready=ready), llm


def test_a_good_answer_is_kept_and_its_deadline_parsed_in_code():
    c, llm = run(good())
    assert c.fallback == "" and c.output is not None and c.output.urgency == "critical"
    assert c.deadline_at == datetime(2026, 10, 22, 9, 30, tzinfo=UTC)  # 15:00 in Kolkata
    assert len(llm.calls) == 1


def test_the_prompt_has_no_memory_no_brief_and_topics_as_ids():
    _, llm = run(good())
    call = llm.calls[0]
    prompt = call["messages"][0]["content"]
    assert "t1: production outage" in prompt and "share_doc_link" in prompt and MSG in prompt
    assert len(call["system"]) == 1 and "never share" not in call["system"][0].text.lower()
    assert set(call) == {"purpose", "model", "system", "messages"}  # no tools, no sampling parameters


def test_a_quote_not_in_the_message_discards_the_output():
    c, _ = run(good(evidence=["the database is on fire"]))
    assert c.output is None and c.fallback == "bad_quote"


def test_quotes_survive_whitespace_differences():
    assert classify.quotes_hold(TriageOutput.model_validate(good(evidence=["returning  500s\nfor every"])), MSG)


def test_unknown_topic_ids_and_ungranted_intents_are_dropped():
    c, _ = run(good(matched_topics=["t1", "t7"], answer_intent="wire_money"))
    assert c.output is not None
    assert c.output.matched_topics == ["t1"] and c.output.answer_intent == ""


def test_a_deadline_not_in_the_message_is_ignored():
    c, _ = run(good(deadline_text="by midnight"))
    assert c.output is not None and c.deadline_at is None


@pytest.mark.parametrize(
    ("answer", "fallback"),
    [
        (BudgetExceeded("today's triage limit", "bucket"), "budget"),
        (ValueError("not json"), "error"),
    ],
)
def test_failures_fall_back_to_rules_only(answer, fallback):
    c, _ = run(answer)
    assert c.output is None and c.fallback == fallback


def test_offline_makes_no_call():
    c, llm = run(good(), ready=False)
    assert c.fallback == "offline" and llm.calls == []


def test_borderline_items_are_resampled_and_agreement_replaces_confidence():
    # high, no deadline, 0.9 → 0.7 × 0.85 × 0.9 = 0.536: within 0.1 of θ 0.6
    border = good(urgency="high", deadline_text="", confidence=0.9)
    c, llm = run(border, good(urgency="normal", deadline_text=""), border, f=feat(text=MSG.split(" Can")[0]))
    assert len(llm.calls) == 3 and c.samples == 3
    assert c.output is not None and c.output.urgency == "high" and c.agreement == pytest.approx(2 / 3, abs=1e-3)


def test_clear_items_are_not_resampled():
    c, llm = run(good(urgency="low", deadline_text="", confidence=0.9))
    assert len(llm.calls) == 1 and c.agreement is None


def test_triage_has_its_own_daily_bucket(settings):
    assert bucket_for("triage") == "triage"
    assert "triage" in settings.budget_daily_by_bucket
    guard = BudgetGuard(10, limits=Limits(daily_by_bucket={"triage": Decimal("0.001")}))
    first = guard.reserve(purpose="triage", model="claude-haiku-5-5", worst_case_usd=Decimal("0.0006"))
    assert first is not None
    with pytest.raises(BudgetExceeded) as refused:
        guard.reserve(purpose="triage", model="claude-haiku-5-5", worst_case_usd=Decimal("0.0006"))
    assert refused.value.period == "bucket"
    # The chat bucket is untouched by triage spend.
    guard.reserve(purpose="chat_turn", model="claude-haiku-5-5", worst_case_usd=Decimal("0.0006"))


def test_time_is_not_borderline_math_when_far_off():
    far = feat(deadline_at=NOW + timedelta(days=3))
    c, _ = run(good(urgency="normal", deadline_text="", confidence=0.5), f=far)
    assert c.output is not None


def test_the_call_asks_for_low_effort_and_a_small_budget():
    """Haiku 5.5 thinks by default and thinking counts against max_tokens: keep it low and terse."""
    seen: dict = {}

    class Spy:
        def complete(self, **kw):
            seen.update(kw)
            return FakeLLM(Script(good())).complete(**{k: v for k, v in kw.items() if k != "effort"})

    c = classify.classify(Spy(), model="claude-haiku-5-5", f=feat(), ready=True)
    assert c.output is not None
    assert seen["effort"] == "low" and seen["max_tokens"] <= 400
    assert "temperature" not in seen and "top_p" not in seen and "top_k" not in seen
    assert "under 120 characters" in seen["system"][0].text
