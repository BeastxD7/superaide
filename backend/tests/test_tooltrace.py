# ruff: noqa: F811
"""Trace recorder and effect-stub replay (issue #110)."""

from __future__ import annotations

from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import Approval, Intention, ToolTrace
from superaide.db.session import session_scope
from superaide.tooltrace.effects import effect_of
from superaide.tooltrace.recorder import Call, calls_of, digest
from superaide.tooltrace.replay import CANNED, Replayer, diff, drill
from tests.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401  (fixture)

STEPS = (
    ("create_reminder", {"task": "Call Priya", "when": "tomorrow at 5pm"}),
    (
        "propose_action",
        {
            "kind": "message_someone_else",
            "summary": "Tell Priya I'm late",
            "rationale": "asked",
            "reaches_a_person": "yes",
        },
    ),
    ("recall", {"query": "flight"}),
)


def script() -> Script:
    steps: list = [tool_use(n, a, f"t{i}") for i, (n, a) in enumerate(STEPS)]
    return Script(*steps, "Done.")


def run_turn(s, replay=None):
    turn = turn_for(s, script(), "do the things")
    turn.replay = replay
    turn.run([{"role": "user", "content": "x"}])
    return turn.inbound.id


def test_every_call_is_traced_as_hashes_only(owner_client):
    with session_scope() as s:
        turn_id = run_turn(s)
        rows = s.scalars(select(ToolTrace).where(ToolTrace.turn_id == turn_id).order_by(ToolTrace.seq)).all()
        assert [r.tool for r in rows] == ["create_reminder", "propose_action", "recall"]
        assert [r.effect for r in rows] == ["write_own", "write_own", "read_private"]
        assert rows[0].args_hash == digest(STEPS[0][1]) and len(rows[0].result_hash) == 64
        assert all(not r.replay and r.model for r in rows)
        stored = " ".join(str(v) for r in rows for v in (r.tool, r.args_hash, r.result_hash, r.skills))
        assert "Priya" not in stored  # no content, ever


def test_recording_can_be_switched_off(owner_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "trace_recorder", False)
    with session_scope() as s:
        run_turn(s)
        assert s.scalars(select(ToolTrace)).all() == []


def test_replay_changes_nothing_and_reproduces_the_call_sequence_every_time(owner_client):
    with session_scope() as s:
        live = calls_of(s, run_turn(s))
        assert s.scalar(select(Intention.text)) == "Call Priya" and len(s.scalars(select(Approval)).all()) == 1
    for _ in range(50):  # deterministic: 50 of 50
        with session_scope() as s:
            before_i, before_a = len(s.scalars(select(Intention)).all()), len(s.scalars(select(Approval)).all())
            r = Replayer()
            turn_id = run_turn(s, r)
            assert r.calls == live and r.stubbed == 2
            assert calls_of(s, turn_id, replay=True) == live
            assert (len(s.scalars(select(Intention)).all()), len(s.scalars(select(Approval)).all())) == (
                before_i,
                before_a,
            )  # the executor stubbed both writes
            s.rollback()


def test_a_snapshot_answers_reads_and_unknown_tools_are_stubbed_as_sends():
    r = Replayer(snapshot={("recall", digest({"query": "x"})): "recorded"})
    assert r.handle("recall", {"query": "x"}, lambda: ("live", False)) == ("recorded", False)
    assert r.handle("recall", {"query": "y"}, lambda: ("live", False)) == ("live", False)
    assert r.handle("brand_new_tool", {}, lambda: ("DID IT", False)) == (CANNED, False)
    assert effect_of("brand_new_tool") == "send" and r.stubbed == 1


def test_diff_shows_only_names_and_hashes():
    a = Call("create_event", "write_external", "h1")
    b = Call("gmail_send", "send", "h2")
    c = Call("gmail_send", "send", "h3")
    assert diff([a, b], [a, b]).same
    d = diff([a, b], [a, c])
    assert d.only_before == [b] and d.only_after == [c] and d.summary() == "2 things would be done differently."
    assert drill([([a], [a])] * 195 + [([a], [b])] * 5) == "5 of 200 would change."
