"""Streaming chat: Ava's progress (reasoning, narration, tool calls, text) arrives live,
and what she did is saved with the reply and remembered on the next turn."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from superaide.app.stream import sse, stream_turn
from superaide.brain.loop import narrate, outcome
from superaide.brain.turn import RequestedAction, build_messages
from superaide.memory.models import Event
from superaide.pipeline import HandleResult
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import Script, note, owner_client, tool_use, turn_for  # noqa: F401

__all__ = ["owner_client"]


def events_of(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for chunk in body.split("\n\n"):
        lines = [line for line in chunk.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        kind = next(line[6:].strip() for line in lines if line.startswith("event:"))
        data = json.loads("".join(line[5:].strip() for line in lines if line.startswith("data:")))
        out.append((kind, data))
    return out


def test_the_loop_narrates_each_tool_call_and_streams_the_answer(owner_client):
    from superaide.app.accounts import owner as hired_owner

    seen: list[tuple[str, dict[str, Any]]] = []
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        note(s, person.id, "My gym is Cult Fit in Indiranagar")
        script = Script(
            [
                {"type": "thinking", "thinking": "Check the notes first.", "signature": "sig"},
                {"type": "text", "text": "Let me check your notes."},
                *tool_use("recall", {"query": "gym"}),
            ],
            "Your gym is Cult Fit.",
        )
        turn = turn_for(s, script, "Where is my gym?")
        turn._emit = lambda kind, data: seen.append((kind, data))
        result = turn.run([{"role": "user", "content": "Where is my gym?"}])

    kinds = [k for k, _ in seen]
    assert kinds[0] == "status"
    assert ("reasoning", {"delta": "Check the notes first."}) in seen
    # What she wrote before the tool call is narration, not part of the answer.
    assert ("narration", {"text": "Let me check your notes."}) in seen
    tools = [d for k, d in seen if k == "tool"]
    # The recall, then the source check (the uncited answer restates the note, so it gets cited).
    assert [(t["name"], t["state"]) for t in tools] == [
        ("recall", "running"), ("recall", "done"), ("verify", "running"), ("verify", "done"),
    ]  # fmt: skip
    assert tools[0]["label"] == "Searching your notes and messages for “gym”"
    assert tools[1]["detail"] == "Found 1 source"
    assert kinds.index("narration") < kinds.index("tool") < len(kinds) - 1
    assert ("text", {"delta": "Your gym is Cult Fit."}) in seen
    assert result.text == "Your gym is Cult Fit. [1]"
    assert result.reasoning == "Check the notes first."
    assert result.activity[:2] == [
        {"kind": "say", "label": "Let me check your notes."},
        {
            "kind": "tool",
            "name": "recall",
            "state": "done",
            "label": "Searching your notes and messages for “gym”",
            "detail": "Found 1 source",
        },
    ]


def test_a_failed_tool_is_shown_as_an_error(owner_client):
    seen: list[tuple[str, dict[str, Any]]] = []
    with session_scope() as s:
        script = Script(tool_use("create_reminder", {"task": "Call Priya", "when": "someday"}), "When?")
        turn = turn_for(s, script, "remind me to call Priya someday")
        turn._emit = lambda kind, data: seen.append((kind, data))
        turn.run([{"role": "user", "content": "remind me to call Priya someday"}])
    done = [d for k, d in seen if k == "tool" and d["state"] != "running"]
    assert done[0]["state"] == "error" and "Couldn't read the time" in done[0]["detail"]


def test_without_a_listener_nothing_is_streamed_and_nothing_breaks(owner_client):
    with session_scope() as s:
        script = Script(tool_use("recall", {"query": "x"}), "Nothing on that.")
        result = turn_for(s, script).run([{"role": "user", "content": "x?"}])
    assert result.text == "Nothing on that." and result.activity[0]["detail"] == "Nothing matched"


def test_a_listener_that_fails_never_breaks_the_turn(owner_client):
    def broken(kind: str, data: dict[str, Any]) -> None:
        raise BrokenPipeError

    with session_scope() as s:
        turn = turn_for(s, Script("Hello."))
        turn._emit = broken
        assert turn.run([{"role": "user", "content": "hi"}]).text == "Hello."


def test_narration_reads_like_a_person():
    assert narrate("web_search", {"query": "weather Bengaluru"}) == "Searching the web for “weather Bengaluru”"
    assert narrate("create_reminder", {"task": "Gym", "when": "7pm"}) == "Setting a reminder: Gym, 7pm"
    assert narrate("calendar_events", {}) == "Checking your calendar"
    assert narrate("mystery", {}) == "Using mystery"
    assert outcome("recall", [{}, {}], False) == "Found 2 sources"
    assert outcome("create_reminder", "Reminder set: Gym, today at 19:00.\nmore", False) == (
        "Reminder set: Gym, today at 19:00."
    )


def test_stream_turn_forwards_events_then_done_and_survives_errors():
    def ok(emit):
        emit("status", {"text": "Thinking"})
        emit("text", {"delta": "Hi"})
        return HandleResult("known", "Hi", ["steps:1"])

    body = "".join(stream_turn(ok))
    assert body.startswith(": open")
    assert events_of(body) == [
        ("status", {"text": "Thinking"}),
        ("text", {"delta": "Hi"}),
        ("done", {"mode": "known", "reply": "Hi", "notes": ["steps:1"]}),
    ]

    def boom(emit):
        raise RuntimeError("secret detail")

    failed = "".join(stream_turn(boom))
    assert events_of(failed) == [("error", {"message": "Something went wrong on my side. Please try again."})]
    assert "secret detail" not in failed
    assert sse("text", {"delta": "é"}) == 'event: text\ndata: {"delta": "é"}\n\n'


def test_the_stream_endpoint_shows_the_work_and_saves_it_with_the_reply(owner_client, model):
    model.next_action = RequestedAction(kind="message_someone_else", summary="Tell Priya I'm late")
    model.reply = "It's waiting for your OK."
    res = owner_client.post("/app/chat/stream", json={"text": "Tell Priya I'm running late"})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = events_of(res.text)
    tools = [d for k, d in events if k == "tool"]
    assert [t["name"] for t in tools] == ["propose_action", "propose_action"]
    assert tools[0]["label"] == "Asking for your approval: Tell Priya I'm late"
    assert events[-1][0] == "done" and events[-1][1]["reply"] == "It's waiting for your OK."
    assert "".join(d["delta"] for k, d in events if k == "text") == "It's waiting for your OK."

    last = owner_client.get("/app/messages").json()[-1]
    assert last["from"] == "agent" and last["text"] == "It's waiting for your OK."
    assert last["activity"][0]["name"] == "propose_action" and last["activity"][0]["state"] == "done"


def test_the_stream_endpoint_needs_the_owner_and_a_message(owner_client):
    assert owner_client.post("/app/chat/stream", json={"text": "  "}).status_code == 422
    owner_client.post("/auth/logout")
    assert owner_client.post("/app/chat/stream", json={"text": "hi"}).status_code == 401


def test_the_next_turn_is_told_what_tools_the_last_reply_used():
    ev_in = Event(direction="in", content="remind me at 7pm")
    ev_out = Event(
        direction="out",
        content="Done.",
        meta={"activity": [{"kind": "tool", "label": "Setting a reminder: Gym, 7pm", "detail": "Reminder set"}]},
    )
    msgs = build_messages([ev_in, ev_out], "did you set it?")
    assert msgs[1] == {
        "role": "assistant",
        "content": "[Tools used for this reply: Setting a reminder: Gym, 7pm -> Reminder set]\nDone.",
    }


def test_a_gap_of_hours_is_marked_so_old_relative_times_are_not_read_as_now():
    # Live Telegram 2026-10-22: reminders listed at 03:01 as "today at 2:22 am"; at 11:01 "Cool!"
    # got "my system clock shows 11:01 but the app timestamps show 02:22… should I check?"
    from datetime import UTC, datetime

    from superaide.intentions.when import zone

    tz = zone("Asia/Kolkata")
    t0 = datetime(2026, 10, 22, 3, 0, tzinfo=tz).astimezone(UTC)
    history = [
        Event(direction="in", content="what's on my list?", created_at=t0),
        Event(direction="out", content="Drink water, today at 2:22 am.", created_at=t0 + timedelta(minutes=1)),
        Event(direction="in", content="ok", created_at=t0 + timedelta(minutes=5)),  # under 30 min: no line
        Event(direction="in", content="Cool!", created_at=t0 + timedelta(hours=8, minutes=1)),
    ]
    msgs = build_messages(history, "Cool!", tz=tz)
    assert msgs[-1]["content"] == "ok\n[— 8 hours later, Thu 11:01 —]\nCool!"
    assert "later" not in msgs[0]["content"] and "later" not in msgs[1]["content"]
    assert build_messages(history, "Cool!")[-1]["content"] == "ok\nCool!"  # no zone, no lines


def test_a_gap_line_the_model_echoes_is_stripped():
    from superaide.brain.loop import render

    text, _ = render([{"type": "text", "text": "[— 8 hours later, Thu 11:01 —]\nGlad it helps!"}])
    assert text == "Glad it helps!"
