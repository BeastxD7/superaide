# ruff: noqa: F811
"""#330: the human-like task protocol (TASK_PROTOCOL_ENABLED). The model proposes a plan, code decides."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.brain import taskplan
from superaide.brain.loop import OWNER_RULES
from superaide.platform.config import get_settings
from superaide.platform.db.models import Approval
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401  (fixtures)

PLAN = {
    "goal": "Stop the Bluehost renewal before it is charged",
    "evidence": ["Bluehost mail of 3 Oct: renews 3 Nov", "Their no-reply address is not monitored"],
    "route": "Their support chat, read by a human agent",
    "ability_ok": True,
    "recommendation": "Send them this cancellation note today",
}
ACTION = {"kind": "publish_post", "summary": "Post the note", "rationale": "owner asked"}
MSGS = [{"role": "user", "content": "x"}]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(get_settings(), "task_protocol_enabled", True)
    monkeypatch.setattr(get_settings(), "approval_require_executor", False)


def approvals(s):
    return s.scalars(select(Approval)).all()


def run(script, text="cancel bluehost"):
    with session_scope() as s:
        result = turn_for(s, script, text).run(MSGS)
        return result, approvals(s)


# --- the gates, as plain functions ---------------------------------------------------------------


def test_a_good_plan_passes_when_something_was_read():
    assert isinstance(taskplan.check_plan(PLAN, {"gmail_search"}), taskplan.TaskPlan)


@pytest.mark.parametrize(
    "args, read, why",
    [
        (PLAN, set(), "nothing was read"),
        (PLAN, {"create_reminder"}, "nothing was read"),
        ({**PLAN, "evidence": []}, {"recall"}, "evidence"),
        ({**PLAN, "evidence": ["  "]}, {"recall"}, "evidence"),
        ({**PLAN, "goal": ""}, {"recall"}, "goal"),
        ({k: v for k, v in PLAN.items() if k != "route"}, {"recall"}, "route"),
        ({**PLAN, "ability_ok": "yes"}, {"recall"}, "ability_ok"),
        ({**PLAN, "recommendation": "I'm not sure this will reach a person, but send it"}, {"recall"}, "hedges"),
        ({**PLAN, "extra": 1}, {"recall"}, "malformed"),
    ],
)
def test_a_weak_plan_is_refused(args, read, why):
    out = taskplan.check_plan(args, read)
    assert isinstance(out, str) and why in out


# --- flag off: nothing changes -----------------------------------------------------------------


def test_with_the_flag_off_prompt_tools_and_gate_are_unchanged(owner_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "task_protocol_enabled", False)
    monkeypatch.setattr(get_settings(), "approval_require_executor", False)
    with session_scope() as s:
        script = Script(tool_use("propose_action", ACTION), "Queued.")
        turn = turn_for(s, script)
        blocks = turn.system()
        assert blocks[0].text.endswith(OWNER_RULES)
        assert "record_plan" not in blocks[0].text + blocks[1].text
        assert "record_plan" not in {t["name"] for t in turn.tools()}
        result = turn.run(MSGS)
        assert len(approvals(s)) == 1 and "Plan:" not in result.text
        assert not any(n.startswith("plan:") or n.endswith(":no_plan") for n in result.notes)


# --- flag on ------------------------------------------------------------------------------------


def test_the_protocol_is_in_the_prompt_and_the_tool_is_offered(owner_client, on):
    with session_scope() as s:
        turn = turn_for(s, Script("hi"))
        assert taskplan.PROTOCOL_RULES in turn.system()[0].text
        assert "record_plan" in {t["name"] for t in turn.tools()}


def test_no_approval_without_a_plan(owner_client, on):
    script = Script(tool_use("propose_action", ACTION), "I need to look first.")
    result, rows = run(script)
    assert rows == []
    assert script.seen[1][-1]["content"][0]["is_error"] is True
    assert "NO approval was created" in script.seen[1][-1]["content"][0]["content"]
    assert "action:publish_post:no_plan" in result.notes


def test_a_plan_with_nothing_read_is_refused_and_so_is_the_approval(owner_client, on):
    script = Script(tool_use("record_plan", PLAN, "t1"), tool_use("propose_action", ACTION, "t2"), "ok")
    result, rows = run(script)
    assert rows == [] and "plan:refused" in result.notes and "action:publish_post:no_plan" in result.notes


def test_read_then_plan_then_approval_shows_the_plan_line_and_keeps_the_plan(owner_client, on):
    script = Script(
        tool_use("recall", {"query": "bluehost"}, "t0"),
        tool_use("record_plan", PLAN, "t1"),
        tool_use("propose_action", ACTION, "t2"),
        "I found the renewal mail. I recommend the chat route. Approve the note?",
    )
    result, rows = run(script)
    assert len(rows) == 1
    assert rows[0].payload["plan"]["route"] == PLAN["route"]
    assert result.text.rstrip().splitlines()[-1].startswith("Plan: Stop the Bluehost renewal")
    assert "plan:ok" in result.notes


def test_a_plan_that_says_ava_cannot_execute_the_route_creates_no_approval(owner_client, on):
    script = Script(
        tool_use("recall", {"query": "bluehost"}, "t0"),
        tool_use("record_plan", {**PLAN, "ability_ok": False}, "t1"),
        tool_use("propose_action", ACTION, "t2"),
        "I can't use their chat myself; here is the text to paste.",
    )
    result, rows = run(script)
    assert rows == [] and "action:publish_post:cannot_execute" in result.notes
    assert "Plan:" not in result.text


def test_a_hedged_reply_still_withdraws_the_approval_and_shows_no_plan_line(owner_client, on):
    script = Script(
        tool_use("recall", {"query": "bluehost"}, "t0"),
        tool_use("record_plan", PLAN, "t1"),
        tool_use("propose_action", ACTION, "t2"),
        "Approve? I'm not sure this will reach a person though.",
    )
    result, _ = run(script)
    assert "Plan:" not in result.text and "approval_withdrawn:hedge" in result.notes


def test_a_shared_channel_is_untouched(owner_client, on):
    with session_scope() as s:
        turn = turn_for(s, Script("hi"))
        turn.shared = True
        assert "record_plan" not in {t["name"] for t in turn.tools()}
        assert taskplan.PROTOCOL_RULES not in "".join(b.text for b in turn.system())
