"""Rubber-Stamp Brake (#134): fast, always-yes approvals earn friction; a careful owner never sees it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.platform.db.models import Approval, AuditLog
from superaide.platform.db.session import session_scope
from superaide.policy import brake


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "rubber_stamp_brake_enabled", True)


def add(kind: str, *, seconds: float, status: str = "approved", pending: bool = False, age: int = 0) -> str:
    now = datetime.now(UTC) - timedelta(minutes=age)
    with session_scope() as s:
        a = Approval(kind=kind, payload={"summary": f"{kind} please"}, status="pending" if pending else status)
        s.add(a)
        s.flush()
        a.created_at = now - timedelta(seconds=seconds)
        if not pending:
            a.decided_at = now
        return str(a.id)


def stamp(n: int = 10, seconds: float = 1.0) -> None:
    for i in range(n):
        add("publish_post", seconds=seconds, age=n - i + 1)


def test_off_by_default_nothing_changes(owner):
    stamp()
    pending = add("publish_post", seconds=0, pending=True)
    r = owner.post(f"/app/approvals/{pending}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert owner.get("/app/approvals/brake").status_code == 404


def test_fast_unbroken_yeses_engage_the_brake_and_a_reason_is_required(owner, on):
    stamp()
    pending = add("publish_post", seconds=0, pending=True)
    st = owner.get("/app/approvals/brake").json()
    assert st["engaged"] and st["decisions"] == 10 and st["reject_rate"] == 0.0
    card = next(a for a in owner.get("/app/approvals").json() if a["id"] == pending)
    assert card["needs_reason"] is True

    r = owner.post(f"/app/approvals/{pending}/approve")
    assert r.status_code == 422 and "reason" in r.json()["detail"]
    assert owner.post(f"/app/approvals/{pending}/approve", json={"reason": "short"}).status_code == 422
    with session_scope() as s:
        assert s.get(Approval, __import__("uuid").UUID(pending)).status == "pending"
        assert s.scalar(select(AuditLog).where(AuditLog.action == "brake_held_approval")) is not None

    ok = owner.post(f"/app/approvals/{pending}/approve", json={"reason": "I read the draft, it is fine"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"


def test_rejecting_is_never_held_back(owner, on):
    stamp()
    pending = add("publish_post", seconds=0, pending=True)
    assert owner.post(f"/app/approvals/{pending}/reject").json()["status"] == "rejected"


def test_a_low_risk_card_needs_no_reason_even_when_braked(owner, on):
    stamp()
    pending = add("some_harmless_note", seconds=0, pending=True)
    assert owner.post(f"/app/approvals/{pending}/approve").status_code == 200


def test_slow_careful_approvals_do_not_engage(owner, on):
    stamp(seconds=40)
    assert owner.get("/app/approvals/brake").json()["engaged"] is False
    pending = add("publish_post", seconds=0, pending=True)
    assert owner.post(f"/app/approvals/{pending}/approve").status_code == 200


def test_a_real_rejection_in_the_window_keeps_it_off(owner, on):
    stamp(9)
    add("publish_post", seconds=1, status="rejected", age=1)
    assert owner.get("/app/approvals/brake").json()["engaged"] is False


def test_too_few_decisions_to_judge(owner, on):
    stamp(brake.MIN_DECISIONS - 1)
    assert owner.get("/app/approvals/brake").json()["engaged"] is False


def test_a_smaller_batch_while_braked(owner, on):
    stamp()
    for _ in range(6):
        add("some_harmless_note", seconds=0, pending=True)
    waiting = [a for a in owner.get("/app/approvals").json() if a["status"] == "pending"]
    assert len(waiting) == brake.PENDING_CAP


def test_the_numbers_are_code_not_prompt():
    # The brake reads no model output: the thresholds are module constants a prompt cannot reach.
    assert brake.FAST_MEDIAN_S > 0 and brake.MIN_DECISIONS >= 5
