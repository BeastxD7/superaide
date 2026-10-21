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
from superaide.db.models import ChannelIdentity, Intention, Job, OwnerPreference, Person
from superaide.db.session import session_scope
from superaide.intentions import service as intentions
from superaide.jobs.queue import run_due
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


# --- choosing the channel ------------------------------------------------------------


@pytest.fixture
def pushed(monkeypatch, outbox):
    """Telegram and Slack sends land in the fake messengers instead of the network."""
    monkeypatch.setattr("superaide.proactive.notify.messenger_for", lambda provider: outbox.get(provider))
    return outbox


def link(s, provider: str, key: str = "1001") -> None:
    owner = hired_owner(s)
    assert owner is not None
    s.add(ChannelIdentity(provider=provider, stable_key=key, person_id=owner.id, lifecycle="active"))
    s.flush()


def due_now(intention_id: uuid.UUID) -> None:
    with session_scope() as s:
        r = s.get(Intention, intention_id)
        assert r is not None
        due = r.due_at
    run_due(due + timedelta(seconds=1))


def test_a_reminder_on_telegram_arrives_in_the_app_and_on_telegram(pushed):
    with session_scope() as s:
        link(s, "telegram")
        script = Script(
            tool_use("create_reminder", {"task": "Go to the washroom", "when": "in 1 minute", "channel": "telegram"}),
            "Set.",
        )
        turn = turn_for(s, script, "remind me on telegram")
        turn.run(ASK)
        assert "arriving in the app and on Telegram" in result_of(script)["content"]
        assert turn.activity[0]["label"] == "Setting a reminder on Telegram: Go to the washroom, in 1 minute"
        r = s.scalar(select(Intention))
        assert r is not None and r.channel == "telegram"
        rid = r.id
    due_now(rid)
    assert [m["text"] for m in pushed["telegram"].sent] == ["⏰ Reminder: Go to the washroom"]
    assert pushed["slack"].sent == []
    with session_scope() as s:
        r = s.get(Intention, rid)
        assert r is not None and r.delivered_via == ["app", "telegram"]


def test_a_channel_that_isnt_linked_is_an_error_that_says_how_to_link_it(pushed):
    with session_scope() as s:
        script = Script(
            tool_use("create_reminder", {"task": "Stretch", "when": "in 1 hour", "channel": "telegram"}), "Sorry."
        )
        turn_for(s, script).run(ASK)
        out = result_of(script)
        assert out.get("is_error") is True
        assert "Telegram isn't linked" in out["content"] and "Connections" in out["content"]
        assert s.scalar(select(Intention)) is None


def test_moving_a_reminder_to_slack_after_it_was_set(pushed):
    """'I mean text me on Slack' after the reminder was set."""
    with session_scope() as s:
        link(s, "slack", "U123")
        r = make(s, "Gym")
        key = intentions._job_key(r)
        script = Script(tool_use("update_reminder", {"id": str(r.id), "channel": "slack"}), "Done.")
        turn = turn_for(s, script)
        turn.run(ASK)
        assert "arriving in the app and on Slack" in result_of(script)["content"]
        assert turn.activity[0]["label"] == "Moving your reminder: Gym → on Slack"
        s.flush()
        s.refresh(r)
        assert r.channel == "slack" and jobs(s, r) == {key: "queued"}
        rid = r.id
    due_now(rid)
    assert [m["stable_key"] for m in pushed["slack"].sent] == ["U123"]
    assert pushed["telegram"].sent == []


def test_a_refused_channel_change_leaves_the_reminder_as_it_was(pushed):
    with session_scope() as s:
        r = make(s, "Gym")
        due = r.due_at
        script = Script(
            tool_use("update_reminder", {"id": str(r.id), "when": "in 9 hours", "task": "Run", "channel": "slack"}),
            "Sorry.",
        )
        turn_for(s, script).run(ASK)
        assert result_of(script).get("is_error") is True
        s.flush()
        s.refresh(r)
        assert (r.text, r.due_at, r.channel) == ("Gym", due, "default")


def test_default_still_follows_the_interruption_choice_and_app_stays_in_the_app(pushed):
    with session_scope() as s:
        link(s, "telegram")
        owner = hired_owner(s)
        assert owner is not None
        default = make(s, "Default one", hours=1)
        app_only = intentions.create(
            s, owner, text="App only", due_at=datetime.now(UTC) + timedelta(hours=2), channel="app"
        )
        ids = default.id, app_only.id
    due_now(ids[0])
    assert pushed["telegram"].sent == []  # the owner hasn't chosen Telegram for interruptions
    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        pref = s.scalar(select(OwnerPreference).where(OwnerPreference.person_id == owner.id))
        assert pref is not None
        pref.interrupt_channel = "telegram"
    due_now(ids[1])
    assert pushed["telegram"].sent == []  # "app" means the app only, whatever the default
    with session_scope() as s:
        later = make(s, "Default again", hours=3)
        lid = later.id
    due_now(lid)
    assert [m["text"] for m in pushed["telegram"].sent] == ["⏰ Reminder: Default again"]


def test_ava_is_told_which_channels_are_linked_and_list_shows_each_channel(pushed):
    with session_scope() as s:
        link(s, "telegram")
        owner = hired_owner(s)
        assert owner is not None
        intentions.create(s, owner, text="Tg one", due_at=datetime.now(UTC) + timedelta(hours=1), channel="telegram")
        make(s, "Plain one", hours=2)
        script = Script(tool_use("list_reminders", {}), "Here.")
        turn = turn_for(s, script)
        prompt = turn.system()[1].text
        assert "Channels linked for delivery right now: the app, Telegram." in prompt
        assert "Not linked: Slack" in prompt and "Connections" in prompt
        turn.run(ASK)
        text = result_of(script)["content"]
        assert "Tg one" in text and "arrives in the app and on Telegram (channel: telegram)" in text
        assert "Plain one" in text and "arrives in the app" in text


def test_channel_labels():
    assert narrate("create_reminder", {"task": "Gym", "when": "at 7pm"}) == "Setting a reminder: Gym, at 7pm"
    assert narrate("create_reminder", {"task": "Gym", "when": "at 7pm", "channel": "app"}) == (
        "Setting a reminder in the app: Gym, at 7pm"
    )


def test_moving_to_a_bare_time_keeps_the_reminders_day():
    # Live eval 2026-10-22: "move my dentist reminder to 3pm" moved it from tomorrow to today.
    from superaide.intentions.when import zone

    with session_scope() as s:
        owner = hired_owner(s)
        assert owner is not None
        tz = zone(intentions.owner_zone(s, owner))
        day = (datetime.now(UTC).astimezone(tz) + timedelta(days=2)).date()
        due = datetime(day.year, day.month, day.day, 11, 0, tzinfo=tz)
        r = intentions.create(s, owner, text="Dentist", due_at=due)
        script = Script(tool_use("update_reminder", {"id": str(r.id), "when": "3pm"}), "Moved.")
        turn_for(s, script, "move my dentist reminder to 3pm").run(ASK)
        s.refresh(r)
        assert r.due_at.astimezone(tz) == datetime(day.year, day.month, day.day, 15, 0, tzinfo=tz)
        # A moment that names a day still means that day.
        script = Script(tool_use("update_reminder", {"id": str(r.id), "when": "tomorrow at 9am"}), "Moved.")
        turn_for(s, script, "move it to tomorrow 9am").run(ASK)
        s.refresh(r)
        tomorrow = (datetime.now(UTC).astimezone(tz) + timedelta(days=1)).date()
        assert r.due_at.astimezone(tz).date() == tomorrow and r.due_at.astimezone(tz).hour == 9
