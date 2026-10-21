"""Ava's reminder tools in chat: list, move (rescheduling delivery), reword and cancel."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.brain.loop import narrate
from superaide.db.models import Intention, Job, Person
from superaide.db.session import session_scope
from superaide.intentions import service as intentions
from tests.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401  (fixture re-export)

pytestmark = pytest.mark.usefixtures("owner_client")  # a signed-up owner who hired Ava

ASK = [{"role": "user", "content": "x"}]


def make(s, text: str = "Gym", hours: int = 3, person: Person | None = None) -> Intention:
    who = person or hired_owner(s)
    assert who is not None
    return intentions.create(s, who, text=text, due_at=datetime.now(UTC) + timedelta(hours=hours))


def jobs(s, intention: Intention) -> dict[str, str]:
    rows = s.scalars(select(Job).where(Job.dedupe_key.like(f"intention:{intention.id}:%")))
    return {j.dedupe_key: j.status for j in rows}


def result_of(script: Script, step: int = 1, n: int = 0) -> dict[str, Any]:
    return script.seen[step][-1]["content"][n]


def test_create_reminder_gives_back_the_id():
    with session_scope() as s:
        script = Script(tool_use("create_reminder", {"task": "Gym", "when": "in 2 hours"}), "Set.")
        turn_for(s, script, "remind me").run(ASK)
        made = s.scalar(select(Intention))
        assert made is not None and f"id {made.id}" in result_of(script)["content"]


def test_list_shows_open_reminders_with_ids_soonest_first():
    with session_scope() as s:
        later, sooner = make(s, "Pay rent", hours=30), make(s, "Gym", hours=2)
        done = make(s, "Old thing", hours=5)
        intentions.finish(s, hired_owner(s), done.id, status="done")
        script = Script(tool_use("list_reminders", {}), "Here they are.")
        turn = turn_for(s, script, "what reminders do I have?")
        turn.run(ASK)
        text = result_of(script)["content"]
        assert text.index(str(sooner.id)) < text.index(str(later.id))
        assert "Gym" in text and "Pay rent" in text and "Old thing" not in text
        assert turn.activity[0]["label"] == "Checking your reminders"


def test_list_with_nothing_open_says_so():
    with session_scope() as s:
        script = Script(tool_use("list_reminders", {}), "None.")
        turn_for(s, script).run(ASK)
        assert result_of(script)["content"] == "No open reminders."


def test_moving_a_reminder_reschedules_its_delivery_job():
    with session_scope() as s:
        r = make(s, "Gym")
        old_key = intentions._job_key(r)
        script = Script(tool_use("update_reminder", {"id": str(r.id), "when": "in 5 hours"}), "Moved.")
        turn = turn_for(s, script, "move gym")
        turn.run(ASK)
        out = result_of(script)
        assert not out.get("is_error") and "Reminder updated" in out["content"]
        s.flush()
        s.refresh(r)
        assert abs(r.due_at - (datetime.now(UTC) + timedelta(hours=5))) < timedelta(minutes=2)
        new_key = intentions._job_key(r)
        assert jobs(s, r) == {old_key: "cancelled", new_key: "queued"}
        job = s.scalar(select(Job).where(Job.dedupe_key == new_key))
        assert job is not None and job.run_at == r.due_at
        assert turn.activity[0]["label"] == "Moving your reminder: Gym → in 5 hours"
        assert "reminder_changed" in turn.notes


def test_moving_back_to_an_earlier_time_revives_that_job():
    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        r = make(s, "Gym")
        first_due, first_key = r.due_at, intentions._job_key(r)
        intentions.change(s, owner, r.id, due_at=first_due + timedelta(hours=1))
        intentions.change(s, owner, r.id, due_at=first_due)
        assert jobs(s, r)[first_key] == "queued"
        assert list(jobs(s, r).values()).count("queued") == 1


def test_rewording_keeps_the_job_and_delivers_the_new_text():
    with session_scope() as s:
        r = make(s, "Gym")
        key = intentions._job_key(r)
        script = Script(tool_use("update_reminder", {"id": str(r.id), "task": "Gym with Priya"}), "Done.")
        turn = turn_for(s, script)
        turn.run(ASK)
        s.flush()
        s.refresh(r)
        assert r.text == "Gym with Priya" and jobs(s, r) == {key: "queued"}
        assert turn.activity[0]["label"] == "Editing your reminder: Gym → “Gym with Priya”"


def test_cancelling_cancels_the_delivery_job():
    with session_scope() as s:
        r = make(s, "Gym")
        script = Script(tool_use("cancel_reminder", {"id": str(r.id)}), "Cancelled.")
        turn = turn_for(s, script)
        turn.run(ASK)
        assert result_of(script)["content"] == "Reminder cancelled: Gym."
        s.flush()
        s.refresh(r)
        assert r.status == "cancelled" and set(jobs(s, r).values()) == {"cancelled"}
        assert turn.activity[0]["label"] == "Cancelling your reminder: Gym"


def test_unknown_malformed_or_someone_elses_id_is_an_error(manager):
    with session_scope() as s:
        theirs = make(s, "Their thing", person=s.get(Person, manager.id))
        bad_ids = [str(uuid.uuid4()), "rem-1", str(theirs.id)]
        steps: list[Any] = [tool_use("cancel_reminder", {"id": i}, f"c{n}") for n, i in enumerate(bad_ids)]
        steps += [tool_use("update_reminder", {"id": str(theirs.id), "when": "in 1 hour"}, "u"), "Sorry."]
        script = Script(*steps)
        turn_for(s, script).run(ASK)
        for step in range(1, 5):
            out = result_of(script, step)
            assert out.get("is_error") is True and "No such reminder" in out["content"]
        s.flush()
        s.refresh(theirs)
        assert theirs.status == "open" and set(jobs(s, theirs).values()) == {"queued"}


def test_a_bad_time_or_nothing_to_change_is_an_error_and_changes_nothing():
    with session_scope() as s:
        r = make(s, "Gym")
        due, key = r.due_at, intentions._job_key(r)
        script = Script(
            tool_use("update_reminder", {"id": str(r.id), "when": "someday maybe"}, "a"),
            tool_use("update_reminder", {"id": str(r.id)}, "b"),
            "Which time?",
        )
        turn_for(s, script).run(ASK)
        assert "Couldn't read the time" in result_of(script, 1)["content"]
        assert result_of(script, 1).get("is_error") is True
        assert result_of(script, 2).get("is_error") is True
        s.flush()
        s.refresh(r)
        assert r.due_at == due and jobs(s, r) == {key: "queued"}


def test_a_closed_reminder_cannot_be_moved_or_cancelled():
    with session_scope() as s:
        r = make(s, "Gym")
        intentions.finish(s, hired_owner(s), r.id, status="done")
        script = Script(
            tool_use("update_reminder", {"id": str(r.id), "when": "in 1 hour"}, "a"),
            tool_use("cancel_reminder", {"id": str(r.id)}, "b"),
            "It's done already.",
        )
        turn_for(s, script).run(ASK)
        for step in (1, 2):
            out = result_of(script, step)
            assert out.get("is_error") is True and re.search(r"already done", out["content"])


def test_narrate_labels():
    assert narrate("update_reminder", {"current": "Gym", "when": "tomorrow 7pm"}) == (
        "Moving your reminder: Gym → tomorrow 7pm"
    )
    assert narrate("cancel_reminder", {"current": "Gym"}) == "Cancelling your reminder: Gym"
    assert narrate("cancel_reminder", {}) == "Cancelling your reminder: a reminder"
