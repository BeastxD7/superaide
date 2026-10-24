"""Owner Twin (#119): predicts approve/reject from past decisions, honestly scored, and reads only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from superaide.db.models import Approval
from superaide.db.session import session_scope
from superaide.trust import twin


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "owner_twin_enabled", True)


def decide(rows: list[tuple[str, str]]) -> None:
    base = datetime.now(UTC) - timedelta(days=2)
    with session_scope() as s:
        for i, (kind, status) in enumerate(rows):
            at = base + timedelta(minutes=i)
            a = Approval(kind=kind, payload={}, status=status)
            s.add(a)
            s.flush()
            a.created_at, a.decided_at = at - timedelta(seconds=30), at


def test_off_by_default(owner):
    assert owner.get("/app/twin").status_code == 404


def test_no_history_means_not_ready(owner, on):
    r = owner.get("/app/twin").json()
    assert r["decisions"] == 0 and r["gate"]["ready"] is False and r["accuracy"] is None


def test_a_consistent_owner_is_predicted_and_the_gate_opens(owner, on):
    decide([("gmail_send", "approved")] * 25 + [("publish_post", "rejected")] * 25)
    r = owner.get("/app/twin").json()
    assert r["decisions"] == 50 and r["accuracy"] >= 0.9 and r["gate"]["ready"] is True
    assert r["per_kind"]["publish_post"]["yes_rate"] == 0.0
    assert all(b["n"] > 0 for b in r["bins"])


def test_an_unpredictable_owner_does_not_pass(owner, on):
    decide([("gmail_send", "approved" if i % 2 else "rejected") for i in range(40)])
    g = owner.get("/app/twin").json()["gate"]
    assert g["ready"] is False and g["reasons"]


def test_few_decisions_are_not_enough_even_if_perfect(owner, on):
    decide([("gmail_send", "approved")] * 10)
    g = owner.get("/app/twin").json()["gate"]
    assert g["ready"] is False and any("needs 30" in r for r in g["reasons"])


def test_it_is_scored_before_it_sees_each_answer():
    # The very first prediction cannot know the owner's habits: it is 0.5, not a peek at the answer.
    t = twin.Twin()
    assert t.p_yes("anything") == 0.5
    t.learn("anything", True)
    assert t.p_yes("anything") > 0.5 and t.p_yes("other") > 0.5  # shrinks toward the overall rate


def test_it_only_reads(owner, on):
    decide([("gmail_send", "approved")] * 12)
    with session_scope() as s:
        before = s.scalar(select(func.count()).select_from(Approval))
    owner.get("/app/twin")
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Approval)) == before
