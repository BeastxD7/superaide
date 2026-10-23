"""Circuit breaker (#140): a burst of deletes or sends freezes Ava; the owner's halt word is handled before
any model sees it. Off by default."""

from __future__ import annotations

from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import Approval, AuditLog
from superaide.db.session import session_scope
from superaide.policy import breaker
from superaide.policy.binding import seal
from superaide.policy.actions import resolve
from superaide.policy.killswitch import is_frozen
from tests.conftest import MANAGER_TG, tg


def on(monkeypatch, limit=3):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "circuit_breaker_enabled", True)
    monkeypatch.setattr(cfg, "circuit_breaker_limit", limit)


def approvals(n: int, kind="delete_memory") -> list:
    with session_scope() as s:
        rows = [Approval(kind=kind, payload={"summary": f"x{i}"}) for i in range(n)]
        for r in rows:
            seal(r)  # a real request is sealed when it is made
        s.add_all(rows)
        s.flush()
        return [r.id for r in rows]


def approve(approval_id) -> str:
    with session_scope() as s:
        a = s.get(Approval, approval_id)
        resolve(s, a, approve=True, decided_by="owner")
        return a.status


def test_off_by_default_300_deletes_all_run():
    assert not get_settings().circuit_breaker_enabled
    ids = approvals(300)
    assert [approve(i) for i in ids].count("approved") == 300
    with session_scope() as s:
        assert not is_frozen(s)


def test_a_burst_of_deletes_executes_at_most_the_limit_then_freezes(monkeypatch):
    on(monkeypatch, limit=3)
    ids = approvals(300)
    done = 0
    for i in ids:
        if approve(i) == "approved":
            done += 1
        else:
            break
    assert done == 3
    with session_scope() as s:
        assert is_frozen(s)
        assert s.scalar(select(AuditLog.id).where(AuditLog.action == "breaker_tripped")) is not None
        held = s.get(Approval, ids[3])
        assert held.status == "pending" and "Held" in held.payload["result"]


def test_other_kinds_are_not_counted(monkeypatch):
    on(monkeypatch, limit=1)
    ids = approvals(5, kind="grant_access")
    assert all(approve(i) == "approved" for i in ids)


def test_the_window_slides(monkeypatch):
    on(monkeypatch, limit=2)
    with session_scope() as s:
        assert breaker.allow(s, "gmail_send", now=1000.0)
        assert breaker.allow(s, "gmail_send", now=1001.0)
        assert breaker.allow(s, "gmail_send", now=1100.0)  # the first two have aged out
        assert not is_frozen(s)


def test_halt_word_from_the_owner_freezes_before_the_model(agent, model, manager, outbox, monkeypatch):
    on(monkeypatch)
    r = agent.handle(tg(MANAGER_TG, " Halt! "))
    assert r.mode == "halted" and model.systems == []
    with session_scope() as s:
        assert is_frozen(s)


def test_halt_word_from_a_stranger_does_nothing(agent, model, manager, monkeypatch):
    on(monkeypatch)
    r = agent.handle(tg("9999", "halt"))
    assert r.mode != "halted"
    with session_scope() as s:
        assert not is_frozen(s)


def test_halt_word_is_ignored_when_the_breaker_is_off(agent, manager):
    agent.handle(tg(MANAGER_TG, "halt"))
    with session_scope() as s:
        assert not is_frozen(s)
