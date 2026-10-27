# ruff: noqa: F811
"""Hash-chained action log and the freeze-only witness (issue #113)."""

from __future__ import annotations

import ast
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text

from superaide.actionlog import chain, witness
from superaide.actionlog.models import ActionLog
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.policy.killswitch import instance_frozen
from tests.pipeline.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(get_settings(), "action_log", True)


def add(s, n=1, **kw):
    for i in range(n):
        chain.append(
            s, **{"kind": "call", "tool": "create_reminder", "effect": "write_own", "args_hash": f"a{i}", **kw}
        )


def test_off_by_default_writes_nothing(monkeypatch):
    monkeypatch.setattr(get_settings(), "action_log", False)
    with session_scope() as s:
        add(s)
        assert chain.count(s) == 0


def test_entries_chain_and_verify():
    with session_scope() as s:
        add(s, 5)
        rows = chain.entries_of(s)
        assert [r.seq for r in rows] == [1, 2, 3, 4, 5] and rows[0].prev_hash == chain.GENESIS
        assert rows[3].prev_hash == rows[2].entry_hash
        assert chain.verify(s).ok


def test_any_rewrite_is_found_at_the_first_broken_entry():
    """Seed 20 tamperings (changed field, removed row, swapped hash) and find every one."""
    rng = random.Random(113)
    for _ in range(20):
        with session_scope() as s:
            s.execute(text("TRUNCATE action_log"))
            add(s, 10)
            rows = chain.entries_of(s)
            victim = rng.choice(rows[1:-1])
            how = rng.choice(["field", "delete", "hash"])
            if how == "field":
                victim.args_hash = "forged"
            elif how == "delete":
                s.delete(victim)
            else:
                victim.entry_hash = "0" * 64
            s.flush()
            v = chain.verify(s)
            assert not v.ok and v.broken_at is not None and v.broken_at >= victim.seq
            s.rollback()


def test_the_merkle_root_changes_when_anything_does():
    with session_scope() as s:
        add(s, 3)
        today = datetime.now(UTC).date()
        before = chain.daily_root(s, today)
        add(s, 1, tool="gmail_send", effect="send", approval_id=None, kind="call")
        assert chain.daily_root(s, today) != before and len(before) == 64
    assert chain.merkle_root([]) == chain.GENESIS


def test_the_witness_leaves_a_clean_log_alone():
    with session_scope() as s:
        add(s, 4)
        add(s, 1, kind="executed", tool="gmail_send", effect="send", approval_id=__import__("uuid").uuid4())
        r = witness.run(s)
        assert r.findings == [] and not r.frozen and not instance_frozen(s)


@pytest.mark.parametrize(
    "rule,setup",
    [
        ("unapproved_send", lambda s: add(s, 1, kind="executed", tool="gmail_send", effect="send")),
        ("retry_loop", lambda s: [add(s, 1, tool="gmail_send", effect="send", args_hash="same") for _ in range(8)]),
    ],
)
def test_the_witness_freezes_on_a_misbehaviour_and_tells_the_owner(owner_client, rule, setup):
    with session_scope() as s:
        setup(s)
        r = witness.run(s)
        assert [f.rule for f in r.findings][0] == rule and r.frozen and instance_frozen(s)
        assert s.scalar(select(AuditLog).where(AuditLog.action == "incident")) is not None
        assert witness.run(s).frozen is False  # already frozen: nothing more to do


def test_a_tampered_chain_freezes(owner_client):
    with session_scope() as s:
        add(s, 4)
        chain.entries_of(s)[1].args_hash = "forged"
        s.flush()
        r = witness.run(s)
        assert r.findings[0].rule == "chain_broken" and instance_frozen(s)


def test_the_gateway_logs_tool_calls_when_on(owner_client):
    with session_scope() as s:
        script = Script(tool_use("create_reminder", {"task": "Call Priya", "when": "tomorrow at 5pm"}), "Done.")
        turn_for(s, script, "remind me").run([{"role": "user", "content": "x"}])
        [row] = s.scalars(select(ActionLog)).all()
        assert row.tool == "create_reminder" and row.kind == "call" and chain.verify(s).ok


def test_the_witness_can_only_freeze():
    """Its code writes nowhere but through set_frozen, audit and notify_owner."""
    src = Path(witness.__file__).read_text()
    tree = ast.parse(src)
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"add", "delete", "execute", "merge", "commit", "add_all"}
    names = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"set_frozen", "audit"} <= names
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not imported & {"Approval", "ActionLog", "EXECUTORS", "resolve", "session_scope"}
