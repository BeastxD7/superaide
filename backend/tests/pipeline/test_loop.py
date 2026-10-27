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
from superaide.identity.resolver import resolve
from superaide.main import create_app
from superaide.memory.audience import audience_for
from superaide.memory.store import get_or_create_context, log_event
from superaide.platform.db.models import Approval, Context, Event, Intention, MemoryItem
from superaide.platform.db.session import session_scope

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
            "/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD}
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
                {
                    "kind": "message_someone_else",
                    "summary": "Tell Priya I'm late",
                    "rationale": "Owner asked",
                    "reaches_a_person": "yes",
                },
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


def test_web_tools_only_with_the_skill_installed_and_web_content_taints_the_turn(owner_client):
    from superaide.skills import registry

    with session_scope() as s:
        t = turn_for(s, Script("ok"), web=True)
        assert "web_search" not in [tool.get("name") for tool in t.tools()]  # not installed yet
        registry.install(s, "superaide/web-research", ["read_public"])
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
        offline = turn_for(s, Script("ok"), web=False)
        assert "web_search" not in [tool.get("name") for tool in offline.tools()]  # no real model, no web
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


def test_a_cited_block_that_only_repeats_the_quote_is_folded_into_the_sentence_before():
    # What Haiku 5.5 actually returned in a live run: the claim, then the quote as its own cited block.
    text, sources = render(
        [
            {"type": "text", "text": "Your flight is Friday at 7:10am. "},
            {
                "type": "text",
                "text": "Jordan's flight to Bangalore is on Friday 23 Oct at 7:10am.",
                "citations": [
                    {
                        "type": "search_result_location",
                        "source": "ava://memory/aaa",
                        "title": "Note",
                        "cited_text": "Jordan's flight to Bangalore is on Friday 23 Oct at 7:10am.",
                    }
                ],
            },
            {"type": "text", "text": "\n\nArjun is picking you up."},
        ]
    )
    assert text == "Your flight is Friday at 7:10am. [1]\n\nArjun is picking you up."
    assert [s.uri for s in sources] == ["ava://memory/aaa"]


def test_a_quote_the_sentence_introduces_stays_quoted_without_a_stray_full_stop():
    cite = {"type": "search_result_location", "source": "ava://msg/web/1", "title": "You", "cited_text": "gym at 12pm"}
    text, _ = render(
        [
            {"type": "text", "text": "Your note from 21 Oct: "},
            {"type": "text", "text": "gym at 12pm", "citations": [cite]},
            {"type": "text", "text": "."},
        ]
    )
    assert text == "Your note from 21 Oct: “gym at 12pm” [1]."


def test_a_quote_after_an_unpunctuated_introduction_is_kept_not_dropped():
    # Live eval 2026-10-22: "Your notebook lists [1] The note has no date…" with the answer gone.
    cite = {
        "type": "search_result_location",
        "source": "ava://memory/bbb",
        "title": "Note",
        "cited_text": "Jordan's manager is Arjun Mehta.",
    }
    text, _ = render(
        [
            {"type": "text", "text": "Your notebook lists "},
            {"type": "text", "text": "Jordan's manager is Arjun Mehta.", "citations": [cite]},
            {"type": "text", "text": " The note has no date."},
        ]
    )
    assert "Arjun Mehta" in text
    assert text.startswith("Your notebook lists “Jordan's manager is Arjun Mehta.” [1]")


def test_a_tools_used_line_the_model_copied_is_removed_from_the_reply():
    # Live eval 2026-10-22: the model began its reply with the app's own record line.
    text, _ = render(
        [{"type": "text", "text": "[Tools used for this reply: create_reminder]\nDone. Call mom tomorrow at 7:00 pm."}]
    )
    assert text == "Done. Call mom tomorrow at 7:00 pm."
    text, _ = render([{"type": "text", "text": "[On Telegram · Wed 19:13] You said hi."}])
    assert text == "You said hi."


def test_email_requests_are_named_as_approvals_in_the_prompt_and_tool():
    from superaide.brain.loop import ACTION_TOOL, LEARNS, OWNER_RULES

    assert "email" in ACTION_TOOL["description"] and "message_someone_else" in ACTION_TOOL["description"]
    assert (
        "email" in OWNER_RULES
        and "gmail_send" in OWNER_RULES
        and "gmail_compose" in OWNER_RULES
        and "NO approval was created" in OWNER_RULES
    )
    assert "forget it by your next conversation" in LEARNS and "say yes plainly" in LEARNS


def test_an_uncited_answer_from_a_found_note_gets_the_citation_in_code(owner_client):
    # Live eval 2026-10-22: "Your manager is Arjun Mehta." with no [n], from a pre-recalled note.
    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        m = note(s, person.id, "Jordan's manager is Arjun Mehta.")
        script = Script("Your manager is Arjun Mehta. Anything else?")
        result = turn_for(s, script, text="who is my manager?").run([{"role": "user", "content": "who is my manager?"}])
    assert result.text == "Your manager is Arjun Mehta. [1] Anything else?"
    assert [c.uri for c in result.sources] == [f"ava://memory/{m.id}"]
    assert "cite_added:1" in result.notes


def test_chit_chat_next_to_a_found_note_is_not_cited():
    from superaide.brain.verify import uncited_restatements

    notes = ["Jordan's manager is Arjun Mehta."]
    assert uncited_restatements("Thanks! Have a great day.", notes) == []
    assert uncited_restatements("You're welcome, Jordan.", notes) == []
    assert uncited_restatements("Arjun Mehta is your manager.", notes) == [("Arjun Mehta is your manager.", 0)]
    assert uncited_restatements("Arjun Mehta is your manager. [1]", notes) == []
    # A short answer from a long learned note (live eval: "Anya's birthday is on 14 March.").
    long_note = ["Jordan's sister Anya's birthday is on 14 March, and she turns 30 this year."]
    answer = "Anya's birthday is on 14 March."
    assert uncited_restatements(answer, long_note) == [(answer, 0)]
    assert uncited_restatements("Happy to help with Anya's plans.", long_note) == []


def test_the_after_midnight_note_tells_the_model_to_keep_the_reminder(owner_client):
    # Live eval 2026-10-22: at 00:30 "tomorrow at 8" was set for 8 am that morning (right), then the
    # note's "if they meant the day after, set it again" led the model to move it a day later.
    from datetime import datetime

    import time_machine

    from superaide.brain.loop import OWNER_RULES
    from superaide.intentions.when import zone

    tz = "Asia/Kolkata"
    night = datetime(2026, 10, 23, 0, 30, tzinfo=zone(tz))
    with session_scope() as s:
        turn = turn_for(s, Script("ok"), "ping me tomorrow at 8")
        with time_machine.travel(night):
            note_text = turn._after_midnight("tomorrow at 8", night.replace(hour=8), tz)
    assert "don't change it" in note_text and "set it again" not in note_text
    assert "newer one wins" in OWNER_RULES
    assert "Don't volunteer corrections of your earlier replies" in OWNER_RULES


# --- a reply cut off at max_tokens (issue #88) ----------------------------------------------------------


def cut(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"content": list(blocks), "stop_reason": "max_tokens"}


def said(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def test_a_reply_cut_off_at_max_tokens_is_retried_with_more_room(owner_client):
    with session_scope() as s:
        script = Script(
            cut(said("Happy to help. Here is a plan: first we gather what we know, and the")),
            "Happy to help. Here is a plan: first we gather what we know, then we decide.",
        )
        turn = turn_for(s, script)
        calls: list[int] = []
        real = turn.llm.step
        turn.llm.step = lambda **kw: (calls.append(kw["max_tokens"]), real(**kw))[1]  # type: ignore[method-assign]
        result = turn.run([{"role": "user", "content": "When is my flight?"}])
    assert result.text == "Happy to help. Here is a plan: first we gather what we know, then we decide."
    assert len(calls) == 2 and calls[1] > calls[0]  # the second try gets a bigger budget
    assert "max_tokens_retry" in result.notes
    assert len(script.seen) == 2 and script.seen[0] == script.seen[1]  # same question, not the half answer


def test_a_reply_still_cut_off_after_the_retry_says_so_instead_of_passing_as_complete(owner_client):
    with session_scope() as s:
        script = Script(
            cut(said("Happy to help. Here is a plan: first we gather what we know, and the")),
            cut(said("Happy to help. Here is a plan: first we gather what we know, and the")),
        )
        result = turn_for(s, script).run([{"role": "user", "content": "When is my flight?"}])
    assert result.text.startswith("Happy to help. Here is a plan: first we gather what we know, and the")
    assert "ran out of room" in result.text and "carry on" in result.text
    assert "max_tokens" in result.notes


def test_an_answer_that_spent_its_whole_budget_thinking_is_never_sent_as_an_ellipsis(owner_client):
    thinking = {"type": "thinking", "thinking": "hmm " * 50, "signature": "sig"}
    with session_scope() as s:
        script = Script(cut(thinking), cut(thinking))
        result = turn_for(s, script).run([{"role": "user", "content": "When is my flight?"}])
    assert result.text != "…" and "ran out of room" in result.text


def test_a_tool_call_cut_off_at_max_tokens_is_not_run(owner_client):
    half = {"type": "tool_use", "id": "toolu_9", "name": "create_reminder", "input": {"task": "Call"}}
    with session_scope() as s:
        script = Script(cut(said("Setting that up."), half), cut(said("Setting that up."), half))
        result = turn_for(s, script, "remind me to call Priya").run([{"role": "user", "content": "x"}])
        assert s.scalar(select(Intention.text)) is None  # a half-written call is never carried out
    assert "ran out of room" in result.text
