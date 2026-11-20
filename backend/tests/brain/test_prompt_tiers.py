# ci-module: brain
"""Full, compact and minimal prompts: chosen in code from the window and the turn, smaller never wider (#641)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select

from superaide.brain import context_window as cw
from superaide.brain import prompt_tiers as pt
from superaide.brain.llm import FakeLLM
from superaide.brain.loop import OWNER_RULES
from superaide.brain.models import LlmProvider
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import Script, owner_client, tool_use, turn_for  # noqa: F401  (fixture)

CHARS_PER_TOKEN = 4.0  # measured on qwen3 (the research report): 4.3 to 4.5 for prompt text; this is a little cautious


def fixed_tokens(call: dict[str, Any]) -> int:
    """The part of a prompt that does not depend on the conversation: system blocks and tool definitions."""
    chars = sum(len(b.text) for b in call["system"]) + (len(json.dumps(call["tools"])) if call["tools"] else 0)
    return round(chars / CHARS_PER_TOKEN)


def W(tokens: int, source: str = "override") -> cw.WindowInfo:
    return cw.WindowInfo(tokens, source)


@pytest.mark.parametrize(
    ("window", "text", "pending", "tier"),
    [
        (W(200_000), "hi", False, pt.FULL),  # Claude is never changed
        (W(200_000), "tell Meera I'm busy", True, pt.FULL),
        (W(100_000), "hello", False, pt.FULL),
        (W(32_768, "default"), "hi", False, pt.FULL),  # a hosted model nobody described keeps its tools
        (W(32_768), "what is on my calendar?", False, pt.COMPACT),
        (W(16_384), "when is my flight?", False, pt.COMPACT),
        (W(8_000), "when is my flight?", False, pt.COMPACT),
        (W(16_384), "hi", False, pt.MINIMAL),  # small talk with nothing pending
        (W(16_384), "ok, thanks!", False, pt.MINIMAL),
        (W(16_384), "hi", True, pt.COMPACT),  # something is waiting: not small talk
        (W(7_999), "when is my flight?", False, pt.MINIMAL),
        (W(4_096, "default"), "when is my flight?", False, pt.MINIMAL),
    ],
)
def test_the_tier_comes_from_the_window_and_the_kind_of_turn(window, text, pending, tier):
    assert pt.select_tier(window, text, pending=pending) == tier


@pytest.mark.parametrize(
    ("text", "talk"),
    [
        ("hi", True), ("Hello!", True), ("ok thanks", True), ("good morning", True), ("yes", True),
        ("remind me to call Priya", False), ("hi, can you check my mail", False),
        ("thanks for the invoice, send it", False), ("", False),
    ],
)  # fmt: skip
def test_small_talk_is_decided_on_the_words_alone(text, talk):
    assert pt.is_small_talk(text) is talk


def tool(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": "Does a thing. Long policy text that code enforces anyway. " * 5,
        "input_schema": {"type": "object", "properties": {"x": {"type": "string", "description": "y. " * 40}}},
    }


ALL = [tool(n) for n in (
    "recall", "create_reminder", "list_reminders", "update_reminder", "cancel_reminder", "gmail_search", "gmail_send",
    "calendar_events", "create_event", "web_search", "web_fetch", "propose_action", "list_approvals", "inbox",
    "relay_message", "set_presence", "channel_activity",
)]  # fmt: skip


def names(tools: list[dict[str, Any]]) -> list[str]:
    return [t["name"] for t in tools]


def test_the_core_set_alone_for_a_plain_question():
    assert names(pt.choose_tools(ALL, "when is my flight?", waiting=False, pending=False)) == [
        "recall", "create_reminder", "list_approvals",
    ]  # fmt: skip


def test_someone_waiting_and_a_message_to_pass_on_offer_the_inbox_and_the_relay():
    got = names(pt.choose_tools(ALL, "tell Meera I'm busy", waiting=True, pending=False))
    assert {"relay_message", "inbox", "set_presence"} <= set(got) and len(got) <= pt.MAX_COMPACT_TOOLS


def test_at_most_six_tools_and_only_ones_the_turn_already_had():
    text = "email, calendar, search the web, remind me, send, tell, busy, channel"
    got = pt.choose_tools(ALL[:5], text, waiting=True, pending=True)
    assert len(got) <= pt.MAX_COMPACT_TOOLS and set(names(got)) <= set(names(ALL[:5]))


def test_short_descriptions_keep_the_name_and_the_schema_shape():
    [short] = pt.choose_tools([tool("recall")], "x", waiting=False, pending=False)
    assert short["name"] == "recall" and len(short["description"]) <= pt.SHORT_DESCRIPTION
    assert short["input_schema"]["properties"]["x"]["type"] == "string"
    assert len(short["input_schema"]["properties"]["x"]["description"]) <= pt.SHORT_PROPERTY
    assert len(ALL[0]["description"]) > 200, "the full tool definition is not edited in place"


def test_rules_follow_the_tools_and_the_limits_on_what_ava_may_do_always_stay():
    bare = pt.rules_for(OWNER_RULES, {"recall", "create_reminder", "list_approvals"}, history=True)
    assert "You cannot post, grant access" in bare and "recall tool" in bare and "list_approvals" in bare
    assert "relay_message" not in bare and "set_presence" not in bare and "Channel linking" not in bare
    wide = pt.rules_for(OWNER_RULES, {"recall", "relay_message", "gmail_send"}, history=True)
    assert "relay_message" in wide and "gmail_send" in wide
    assert len(bare) < len(OWNER_RULES) * 0.75 and len(bare) / CHARS_PER_TOKEN <= 1_000


def test_history_is_capped_in_whole_turns_newest_kept():
    turns = []
    for i in range(30):
        turns += [
            {"role": "user", "content": f"q{i} " + "x" * 400},
            {"role": "assistant", "content": f"a{i} " + "y" * 400},
        ]
    msgs = [*turns, {"role": "user", "content": "now"}]
    compact, start = pt.cap_history(msgs, len(turns), pt.COMPACT)
    assert 0 < start < len(turns) and compact[0]["role"] == "user" and compact[-1]["content"] == "now"
    assert compact[start - 1]["content"].startswith("a29 ")
    assert sum(len(m["content"]) for m in compact[:start]) / 4 <= pt.COMPACT_HISTORY_TOKENS * 1.1
    minimal, start = pt.cap_history(msgs, len(turns), pt.MINIMAL)
    assert start == 4 and minimal[0]["content"].startswith("q28 ")


# --- a turn in the real loop -------------------------------------------------------------------------------


def local_model(s, window: int) -> str:
    row = s.scalar(select(LlmProvider).where(LlmProvider.key == "ollama"))
    if row is None:
        row = LlmProvider(key="ollama", preset="ollama", base_url="http://localhost:11434/v1")
        s.add(row)
    row.meta = {"context_window": window}
    s.flush()
    return "ollama::qwen3:8b"


def run_turn(window: int, text: str, script: Script | None = None, *, shared: bool = False, history: Any = ()) -> Any:
    """Returns (turn, llm, audit rows) for one owner turn on a local model with this window."""
    with session_scope() as s:
        turn = turn_for(s, script or Script("ok"), text=text)
        turn.model, turn.llm, turn.shared = local_model(s, window), FakeLLM(script or Script("ok")), shared
        turn.run([*history, {"role": "user", "content": text}])
        return turn, turn.llm, list(s.scalars(select(AuditLog).where(AuditLog.action == "prompt_tier")))


def test_a_roomy_model_gets_the_prompt_it_always_got(owner_client):  # noqa: F811
    turn, llm, rows = run_turn(200_000, "hi")
    assert rows == [] and turn.offered is None and len(llm.calls[0]["tools"]) >= 5
    assert "How you work with your owner" in llm.calls[0]["system"][0].text and len(llm.calls[0]["system"]) == 2


def test_compact_keeps_the_prompt_small_and_the_rules_the_tools_need(owner_client):  # noqa: F811
    turn, llm, rows = run_turn(16_384, "when is my flight?")
    call = llm.calls[0]
    assert fixed_tokens(call) <= 2_500 and names(call["tools"]) == ["recall", "create_reminder", "list_approvals"]
    assert "recall tool" in call["system"][0].text and "You cannot post, grant access" in call["system"][0].text
    assert "verified by the app" in call["system"][1].text  # who is speaking is still said
    assert rows[0].detail["tier"] == "compact" and rows[0].detail["tools"] == sorted(turn.offered)


def test_minimal_is_small_talk_with_no_tools_and_the_charter_intact(owner_client):  # noqa: F811
    from superaide.brain.turn import load_charter

    turn, llm, rows = run_turn(16_384, "hi")
    call = llm.calls[0]
    assert call["tools"] == [] and fixed_tokens(call) <= 600, fixed_tokens(call)
    assert call["system"][0].text.startswith(load_charter("Ava")) and "No tools in this reply" in call["system"][0].text
    assert rows[0].detail["tier"] == "minimal"


def test_a_tool_that_was_not_offered_is_refused_in_every_tier(owner_client):  # noqa: F811
    for window, text in ((16_384, "when is my flight?"), (16_384, "hi"), (200_000, "when is my flight?")):
        script = Script(tool_use("gmail_send", {"to": "x@example.com"}, "t1"), "ok")
        turn, llm, _ = run_turn(window, text, script)
        result = script.seen[1][-1]["content"][0]
        assert result.get("is_error") is True, (window, text, result)
        if window < 100_000:
            assert "isn't available for this message" in result["content"]


def test_compact_chooses_the_relay_and_the_inbox_when_a_visitor_is_waiting(owner_client, monkeypatch):  # noqa: F811
    waiting = "People waiting for the owner right now: 1 (people you don't know): Meera on Telegram, waiting 5 min."
    monkeypatch.setattr("superaide.brain.loop.awareness_line", lambda s: waiting)
    turn, llm, _ = run_turn(16_384, "tell Meera I'm busy")
    offered = set(names(llm.calls[0]["tools"]))
    assert {"relay_message", "inbox"} <= offered and len(offered) <= pt.MAX_COMPACT_TOOLS
    hi_turn, hi_llm, _ = run_turn(16_384, "hi")  # someone is waiting, so "hi" is not small talk
    assert hi_turn.notes[0] == "tier:compact" and {"inbox", "relay_message"} <= set(names(hi_llm.calls[0]["tools"]))
    assert fixed_tokens(hi_llm.calls[0]) <= 2_500


def test_the_tier_is_chosen_without_asking_the_model(owner_client):  # noqa: F811
    turn, llm, _ = run_turn(16_384, "when is my flight?")
    assert len(llm.calls) == 1, "one model call: the tier is code"
