# ruff: noqa: F811
"""The rehearsal engine (#110): the owner's own tasks, local reads only, nothing kept, names and hashes out."""

from __future__ import annotations

from sqlalchemy import func, select

from superaide.app.accounts import owner as hired_owner
from superaide.brain.budget import BudgetExceeded
from superaide.platform.db.models import Intention, Person, ToolTrace
from superaide.platform.db.session import session_scope
from superaide.tooltrace import rehearse
from superaide.tooltrace.recorder import digest
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture)
from tests.rehearsal.rehearsal_support import live_history, results_seen, switch

BASE = rehearse.Variant("current")
NEW = rehearse.Variant("candidate", brief_extra="VARIANT: also remind me of the follow-up.")


def two_step(model: str, system: str, messages):
    """Recalls first; at step 2 the candidate prompt also sets a second reminder."""
    n = results_seen(messages)
    if n == 0:
        return [{"type": "tool_use", "id": "a", "name": "recall", "input": {"query": "flight"}}]
    if n == 1 and "VARIANT" in system:
        return [
            {"type": "tool_use", "id": "b", "name": "create_reminder", "input": {"task": "Follow up", "when": "friday"}}
        ]
    return "Done."


def owner_and_tasks(n=3):
    with session_scope() as s:
        live_history(s, n)
        owner = hired_owner(s)
        return owner.id, [t for t in rehearse.recent_tasks(s, owner, n)]


def test_recent_tasks_are_the_owners_own_messages_with_what_ava_did(agent, owner_client):
    with session_scope() as s:
        live_history(s, 3)
        owner = hired_owner(s)
        tasks = rehearse.recent_tasks(s, owner, 2)
        assert len(tasks) == 2 and all(len(t.recorded) == 2 for t in tasks)
        assert tasks[0].at >= tasks[1].at  # newest first
        assert all(t.text.startswith("Please sort out") for t in tasks)


def test_a_rehearsal_finds_what_a_change_would_do_differently_and_changes_nothing(agent, owner_client):
    switch(agent, two_step)
    with session_scope() as s:
        live_history(s, 3)
        owner = hired_owner(s)
        before = (
            s.scalar(select(func.count()).select_from(Intention)),
            s.scalar(select(func.count()).select_from(ToolTrace)),
        )
        tasks = rehearse.recent_tasks(s, owner, 3)
        run = rehearse.rehearse(agent, s, owner, tasks, [BASE, NEW])
        assert run.stopped is None and len(run.results) == 3
        for r in run.results:
            d = rehearse.pair(r, "current", "candidate")
            assert not d.same and [c.tool for c in d.only_after] == ["create_reminder"] and d.only_before == []
        # Nothing was kept: no reminder, no trace row, even though the candidate "set" one.
        assert (
            s.scalar(select(func.count()).select_from(Intention)),
            s.scalar(select(func.count()).select_from(ToolTrace)),
        ) == before


def test_only_local_reads_run_for_real_everything_else_is_a_canned_answer(agent, owner_client):
    seen = []

    def wide(model, system, messages):
        n = results_seen(messages)
        steps = ["recall", "web_search", "gmail_search", "calendar_events", "gmail_send", "create_reminder"]
        if n < len(steps):
            return [
                {
                    "type": "tool_use",
                    "id": f"t{n}",
                    "name": steps[n],
                    "input": {"query": "q", "task": "x", "when": "tomorrow"},
                }
            ]
        return "Done."

    switch(agent, wide)
    with session_scope() as s:
        live_history(s, 1)
        owner = hired_owner(s)
        tasks = rehearse.recent_tasks(s, owner, 1)
        rep = rehearse.LocalOnlyReplayer()
        real_calls = []
        for name in ["recall", "list_reminders", "web_search", "gmail_search", "calendar_events", "gmail_send"]:
            rep.handle(name, {"q": 1}, lambda n=name: real_calls.append(n) or ("real", False))
        assert real_calls == ["recall", "list_reminders"] and rep.stubbed == 4
        run = rehearse.rehearse(agent, s, owner, tasks, [BASE])
        seen = [c.tool for c in run.results[0].runs["current"]]
        assert seen == ["recall", "web_search", "gmail_search", "calendar_events", "gmail_send", "create_reminder"]


def test_what_comes_back_is_names_and_hashes_never_content(agent, owner_client):
    switch(agent, two_step)
    with session_scope() as s:
        live_history(s, 2)
        owner = hired_owner(s)
        run = rehearse.rehearse(agent, s, owner, rehearse.recent_tasks(s, owner, 2), [BASE, NEW])
        shown = str([rehearse.diff_view(rehearse.pair(r, "current", "candidate")) for r in run.results])
        for secret in ("Please sort out", "Follow up", "Call person", "friday"):
            assert secret not in shown
        assert digest({"task": "Follow up", "when": "friday"})[:12] in shown  # a short hash is what is shown


def test_someone_elses_messages_are_never_rehearsed(agent, owner_client):
    with session_scope() as s:
        live_history(s, 2)
        owner = hired_owner(s)
        other = Person(display_name="Priya Tan", relation="colleague")
        s.add(other)
        s.flush()
        from superaide.platform.db.models import Event

        for e in s.scalars(select(Event).where(Event.direction == "in")):
            e.resolved_person_id = other.id  # as if Priya, not the owner, had written them
        s.flush()
        assert rehearse.recent_tasks(s, owner, 5) == []


def test_a_refused_model_call_ends_the_run_with_what_was_done(agent, owner_client):
    state = {"n": 0}

    def flaky(model, system, messages):
        state["n"] += 1
        if state["n"] > 4:
            raise BudgetExceeded("daily limit")
        return two_step(model, system, messages)

    switch(agent, flaky)
    with session_scope() as s:
        live_history(s, 3)
        owner = hired_owner(s)
        run = rehearse.rehearse(agent, s, owner, rehearse.recent_tasks(s, owner, 3), [BASE])
        assert run.stopped and run.stopped.startswith("spend limit") and 0 < len(run.results) < 3 and run.asked == 3


def test_replay_calls_are_charged_to_the_small_learning_bucket(agent, owner_client):
    from superaide.brain.budget import bucket_for

    llm = switch(agent, two_step)
    with session_scope() as s:
        live_history(s, 1)
        owner = hired_owner(s)
        rehearse.rehearse(agent, s, owner, rehearse.recent_tasks(s, owner, 1), [BASE])
    assert {c["purpose"] for c in llm.calls} == {rehearse.PURPOSE} and bucket_for(rehearse.PURPOSE) == "learning"
