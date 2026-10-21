"""The owner's tool loop: recall with citations, reminders, approvals, web taint, limits."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.app.accounts import web_message
from superaide.brain.llm import FakeLLM
from superaide.brain.loop import MAX_STEPS, OwnerTurn, render
from superaide.context.audience import audience_for
from superaide.db.models import Approval, Context, Event, Intention, MemoryItem
from superaide.db.session import session_scope
from superaide.identity.resolver import resolve
from superaide.main import create_app
from superaide.memory.store import get_or_create_context, log_event

PASSWORD = "correct horse battery"


def tool_use(name: str, args: dict[str, Any], id_: str = "toolu_1") -> list[dict[str, Any]]:
    return [{"type": "tool_use", "id": id_, "name": name, "input": args}]


class Script:
    """A fake Claude that plays scripted steps and records what it was sent."""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.seen: list[list[dict[str, Any]]] = []

    def __call__(self, purpose, system, messages, output_model):
        self.seen.append(messages)
        return self.steps.pop(0)


@pytest.fixture
def owner_client(agent):
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    assert (
        c.post(
            "/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def turn_for(s, script: Script, text: str = "When is my flight?", web: bool = False) -> OwnerTurn:
    person = hired_owner(s)
    assert person is not None
    msg = web_message(person, text)
    sender = resolve(s, msg)
    ctx = get_or_create_context(s, msg)
    aud = audience_for(ctx, sender)
    inbound = log_event(s, context=ctx, direction="in", content=text, audience=aud, sender=sender)
    return OwnerTurn(
        llm=FakeLLM(script),
        model="claude-haiku-5-5",
        agent_name="Ava",
        session=s,
        sender=sender,
        audience=aud,
        brief="",
        inbound=inbound,
        web=web,
    )


def note(s, person_id, content: str, allowed: list | None = None) -> MemoryItem:
    m = MemoryItem(
        content=content,
        subject_person_ids=[person_id],
        allowed_kind="persons",
        allowed_person_ids=allowed if allowed is not None else [person_id],
        min_trust=2,
    )
    s.add(m)
    s.flush()
    return m


def test_render_numbers_sources_and_marks_the_sentences_that_cite_them():
    text, sources = render(
        [
            {
                "type": "text",
                "text": "Your flight is on Friday.",
                "citations": [
                    {
                        "type": "search_result_location",
                        "source": "ava://memory/1",
                        "title": "Note",
                        "cited_text": "flight is on Friday",
                    }
                ],
            },
            {
                "type": "text",
                "text": " Priya prefers mornings.",
                "citations": [
                    {
                        "type": "search_result_location",
                        "source": "ava://msg/telegram/2",
                        "title": "You · telegram",
                        "cited_text": "mornings",
                    },
                    {"type": "search_result_location", "source": "ava://memory/1", "title": "Note", "cited_text": "x"},
                ],
            },
            {
                "type": "text",
                "text": " SFO is busy.",
                "citations": [
                    {
                        "type": "web_search_result_location",
                        "url": "https://example.com/sfo",
                        "title": "SFO",
                        "cited_text": "busy",
                    }
                ],
            },
        ]
    )
    assert text == "Your flight is on Friday. [1] Priya prefers mornings. [2] [1] SFO is busy. [3]"
    assert [(c.kind, c.uri) for c in sources] == [
        ("memory", "ava://memory/1"),
        ("message", "ava://msg/telegram/2"),
        ("web", "https://example.com/sfo"),
    ]


def test_recall_returns_citable_notes_and_the_answer_cites_them(owner_client):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        m = note(s, person.id, "My flight to Bangalore is on Friday at 7am")
        script = Script(
            tool_use("recall", {"query": "flight Bangalore"}),
            [
                {
                    "type": "text",
                    "text": "Friday at 7am.",
                    "citations": [
                        {
                            "type": "search_result_location",
                            "source": f"ava://memory/{m.id}",
                            "title": "Note",
                            "cited_text": "Friday at 7am",
                        }
                    ],
                }
            ],
        )
        result = turn_for(s, script).run([{"role": "user", "content": "When is my flight?"}])
    assert result.text == "Friday at 7am. [1]" and result.sources[0].uri == f"ava://memory/{m.id}"
    tool_result = script.seen[1][-1]["content"][0]
    blocks = tool_result["content"]
    assert blocks[0]["type"] == "search_result" and blocks[0]["source"] == f"ava://memory/{m.id}"
    assert blocks[0]["citations"] == {"enabled": True}
    assert "recall:1" in result.notes


def test_recall_never_returns_what_this_audience_may_not_see(owner_client, colleague):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        note(s, person.id, "Bangalore office lease ends in March", allowed=[colleague.id])  # not the owner's
        s.add(
            Event(
                context_id=s.scalar(select(Context.id)) or _ctx(s),
                direction="in",
                content="Bangalore budget",
                audience_person_ids=[colleague.id],
            )
        )
        script = Script(tool_use("recall", {"query": "Bangalore"}), "I don't have a source for that.")
        result = turn_for(s, script).run([{"role": "user", "content": "Bangalore?"}])
    assert script.seen[1][-1]["content"][0]["content"] == "Nothing in your notes or messages matches that."
    assert result.sources == []


def _ctx(s) -> Any:
    c = Context(provider="slack", tenant_scope="T", external_id="C9", kind="group", audience_class="team")
    s.add(c)
    s.flush()
    return c.id


def test_a_reminder_tool_call_sets_a_reminder_and_a_bad_time_is_an_error(owner_client):
    with session_scope() as s:
        script = Script(
            tool_use("create_reminder", {"task": "Call Priya", "when": "someday maybe"}, "t1"),
            tool_use("create_reminder", {"task": "Call Priya", "when": "tomorrow at 5pm"}, "t2"),
            "Done: tomorrow at 5:00 pm.",
        )
        result = turn_for(s, script, "remind me to call Priya").run([{"role": "user", "content": "x"}])
        first = script.seen[1][-1]["content"][0]
        assert first.get("is_error") is True and "Couldn't read the time" in first["content"]
        assert s.scalar(select(Intention.text)) == "Call Priya"
    assert "reminder" in result.notes


def test_side_effects_become_approvals_with_the_reason_and_risky_ones_are_refused(owner_client):
    with session_scope() as s:
        script = Script(
            tool_use(
                "propose_action",
                {"kind": "message_someone_else", "summary": "Tell Priya I'm late", "rationale": "Owner asked"},
                "a",
            ),
            tool_use(
                "propose_action",
                {"kind": "delete_memory", "summary": "Forget the flight", "rationale": "Owner asked"},
                "b",
            ),
            "Queued the message for your OK; deleting needs the dashboard.",
        )
        result = turn_for(s, script).run([{"role": "user", "content": "x"}])
        [approval] = s.scalars(select(Approval)).all()
        assert approval.kind == "message_someone_else" and approval.payload["rationale"] == "Owner asked"
        assert approval.payload["tainted"] is False
        refused = script.seen[2][-1]["content"][0]
        assert refused["is_error"] is True and "stronger identity check" in refused["content"]
    assert "action:message_someone_else:approval" in result.notes and "action:delete_memory:refused" in result.notes


def test_web_tools_only_when_enabled_and_web_content_taints_the_turn(owner_client):
    with session_scope() as s:
        off = Script("ok")
        turn_for(s, off).run([{"role": "user", "content": "x"}])
        on = Script(
            {
                "content": [
                    {"type": "server_tool_use", "id": "srv", "name": "web_search", "input": {"query": "SFO delays"}},
                    {"type": "web_search_tool_result", "tool_use_id": "srv", "content": []},
                    {"type": "text", "text": "No delays reported."},
                ],
                "stop_reason": "end_turn",
            },
        )
        t = turn_for(s, on, web=True)
        result = t.run([{"role": "user", "content": "SFO delays?"}])
    names = [tool.get("name") for tool in t.tools()]
    assert "web_search" in names and "web_fetch" in names
    assert result.tainted is True


def test_pause_turn_is_resumed_and_the_step_limit_ends_politely(owner_client):
    with session_scope() as s:
        paused = Script({"content": [{"type": "text", "text": "Searching…"}], "stop_reason": "pause_turn"}, "Found it.")
        assert turn_for(s, paused).run([{"role": "user", "content": "x"}]).text == "Found it."
        looping = Script(*[tool_use("recall", {"query": "x"}, f"t{i}") for i in range(MAX_STEPS)])
        result = turn_for(s, looping).run([{"role": "user", "content": "x"}])
    assert "step_limit" in result.notes and "ask me to carry on" in result.text


def test_an_owner_chat_answer_carries_its_sources_to_the_app(owner_client, agent):
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        m = note(s, person.id, "Dentist appointment on Thursday at 4pm")
        uri = f"ava://memory/{m.id}"
    agent.llm.responder = Script(
        tool_use("recall", {"query": "dentist"}),
        [
            {
                "type": "text",
                "text": "Thursday at 4pm.",
                "citations": [
                    {
                        "type": "search_result_location",
                        "source": uri,
                        "title": "Note · about",
                        "cited_text": "Thursday at 4pm",
                    }
                ],
            }
        ],
    )
    r = owner_client.post("/app/chat", json={"text": "When's the dentist?"})
    assert r.json()["reply"] == "Thursday at 4pm. [1]"
    last = owner_client.get("/app/messages").json()[-1]
    assert last["sources"] == [
        {"n": 1, "uri": uri, "kind": "memory", "title": "Note · about", "cited_text": "Thursday at 4pm"}
    ]


def test_colleagues_keep_the_old_path_and_never_reach_the_owner_tools(agent, manager, colleague, model):
    from tests.conftest import COLLEAGUE_SLACK, sl

    model.reply = "I'll pass that on."
    result = agent.handle(sl(COLLEAGUE_SLACK, "remind me to call Priya tomorrow at 5"))
    assert result.reply == "I'll pass that on."
    with session_scope() as s:
        assert s.scalar(select(Intention)) is None
