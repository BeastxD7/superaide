# ci-module: brain
"""A prompt over the model's input budget is cut by rule, in a fixed order, and the cut is audited (#641)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from superaide.brain import context_window as cw
from superaide.brain import prompt_budget as pb
from superaide.brain.llm import FakeLLM, SystemBlock
from superaide.brain.models import LlmProvider
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import Script, owner_client, turn_for  # noqa: F401  (fixture)

CHARTER = SystemBlock("CHARTER: never share the owner's private notes with a visitor. " * 20, cache=True)
WHO = SystemBlock("You are talking to a visitor, not the owner. People waiting: 1. Approval pending: send the invoice.")
SYSTEM = [CHARTER, WHO]
TOOLS = [{"name": "recall", "description": "search notes", "input_schema": {"type": "object"}}]
NOW = {"role": "user", "content": "what is on my calendar?"}


def turns(n: int, size: int = 400) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n):
        out += [
            {"role": "user", "content": f"question {i} " + "q" * size},
            {"role": "assistant", "content": f"answer {i} " + "a" * size},
        ]
    return out


def notes(n: int, size: int = 800) -> dict[str, Any]:
    lead = {"type": "text", "text": "(Notes from your notebook that may be relevant, found automatically:)"}
    found = [
        {
            "type": "search_result",
            "source": f"ava://memory/{i}",
            "title": f"n{i}",
            "content": [{"type": "text", "text": "n" * size}],
        }
        for i in range(n)
    ]
    return {"role": "user", "content": [lead, *found, {"type": "text", "text": NOW["content"]}]}


def budget_for(system, tools, msgs, extra: int) -> int:
    return cw.estimate_request(system, msgs, tools) - extra


def test_a_prompt_under_the_budget_is_untouched():
    msgs = [*turns(3), NOW]
    out, start, trim = pb.fit(SYSTEM, TOOLS, msgs, len(msgs) - 1, 100_000)
    assert out == msgs and start == len(msgs) - 1 and not trim.changed


def test_older_history_goes_first_oldest_first_and_stays_in_whole_turns():
    msgs = [*turns(10), NOW]
    out, start, trim = pb.fit(SYSTEM, TOOLS, msgs, len(msgs) - 1, budget_for(SYSTEM, TOOLS, msgs, 1_500))
    assert (
        trim.dropped == {"history_messages": trim.dropped["history_messages"]}
        and trim.dropped["history_messages"] % 2 == 0
    )
    assert out[0]["role"] == "user" and out[-1] == NOW and start == len(out) - 1
    assert out[0]["content"].startswith(f"question {10 - (len(out) - 1) // 2} ")  # the newest turns survive
    assert trim.after <= trim.budget


def test_recalled_notes_go_before_the_last_four_turns_and_lowest_ranked_first():
    msgs = [*turns(4), notes(5)]
    kept_history = msgs[:-1]
    out, _, trim = pb.fit(SYSTEM, TOOLS, msgs, len(msgs) - 1, budget_for(SYSTEM, TOOLS, msgs, 1_000))
    assert out[:-1] == kept_history, "the last four turns are still there"
    left = [b["source"] for b in out[-1]["content"] if b.get("type") == "search_result"]
    assert 0 < len(left) < 5 and left == [f"ava://memory/{i}" for i in range(len(left))]  # best ranked kept
    assert trim.dropped == {"recalled_notes": 5 - len(left)}
    assert out[-1]["content"][-1] == {"type": "text", "text": NOW["content"]}


def test_when_the_notes_are_all_gone_so_is_their_lead_line_then_the_last_turns_go():
    msgs = [*turns(4), notes(3)]
    out, _, trim = pb.fit(SYSTEM, TOOLS, msgs, len(msgs) - 1, budget_for(SYSTEM, TOOLS, msgs, 4_000))
    assert trim.dropped["recalled_notes"] == 3 and trim.dropped["history_messages"] >= 2
    assert out[-1]["content"] == [{"type": "text", "text": NOW["content"]}]


def test_the_system_tools_and_latest_message_are_never_dropped_even_when_nothing_fits():
    msgs = [*turns(6), notes(4)]
    out, start, trim = pb.fit(SYSTEM, TOOLS, msgs, len(msgs) - 1, 10)
    assert out == [{"role": "user", "content": [{"type": "text", "text": NOW["content"]}]}] and start == 0
    assert trim.over_budget_by > 0, "the fixed parts alone are over: it is said, not hidden"
    assert SYSTEM[0].text.startswith("CHARTER") and "Approval pending" in SYSTEM[1].text  # the inputs were not edited


def test_long_results_of_tools_run_this_turn_are_cut_last_and_the_pairing_stays():
    call = {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "recall", "input": {}}]}
    result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r" * 20_000}]}
    msgs = [*turns(2), NOW, call, result]
    out, start, trim = pb.fit(SYSTEM, TOOLS, msgs, 4, budget_for(SYSTEM, TOOLS, msgs, 4_000))
    assert trim.dropped["tool_results"] == 1 and out[start + 1] == call
    body = out[-1]["content"][0]["content"]
    assert body.startswith("rrr") and body.endswith(pb.TRIMMED) and len(body) < 1_000
    assert result["content"][0]["content"] == "r" * 20_000, "the caller's own message is not edited in place"


def pieces(msgs: list[dict[str, Any]]) -> set[str]:
    return {repr(b) for m in msgs for b in (m["content"] if isinstance(m["content"], list) else [m["content"]])}


def test_a_cut_only_removes_never_adds():
    """The audience safety property: whatever a smaller prompt holds, the full prompt held too."""
    msgs = [*turns(8), notes(6)]
    for extra in (500, 2_000, 6_000, 50_000):
        out, _, _ = pb.fit(SYSTEM, TOOLS, msgs, len(msgs) - 1, budget_for(SYSTEM, TOOLS, msgs, extra))
        assert pieces(out) <= pieces(msgs)


def local_model(s, window: int) -> str:
    s.add(
        LlmProvider(
            key="ollama", preset="ollama", base_url="http://localhost:11434/v1", meta={"context_window": window}
        )
    )
    s.flush()
    return "ollama::qwen3:8b"


def sent_to_the_model(window: int, history: list[dict[str, Any]]) -> tuple[FakeLLM, list[AuditLog]]:
    with session_scope() as s:
        turn = turn_for(s, Script("ok"))
        turn.model, turn.llm = local_model(s, window), FakeLLM(Script("ok"))
        turn.run([*history, NOW])
        return turn.llm, list(s.scalars(select(AuditLog).where(AuditLog.action == "prompt_trimmed")))


def test_a_turn_on_a_small_window_sends_a_prompt_inside_the_budget_and_audits_what_went(owner_client, monkeypatch):  # noqa: F811
    monkeypatch.setattr("superaide.brain.prompt_tiers.select_tier", lambda *a, **k: "full")  # this tests the budget
    history = turns(20, 600)
    with session_scope() as s:
        turn = turn_for(s, Script("ok"))
        core = cw.estimate_request(turn.system(), [NOW], turn.tools())
    window = int((core + 3_000 + 1_200) / 0.9) + 1  # the fixed parts plus about 3,000 tokens of history
    llm, rows = sent_to_the_model(window, history)
    call = llm.calls[0]
    assert cw.estimate_request(call["system"], call["messages"], call["tools"]) <= cw.input_budget(window, 1_200)
    assert call["messages"][-1] == NOW and call["messages"][0]["role"] == "user"
    assert "CHARTER" not in call["system"][0].text and "Ava" in call["system"][0].text  # the real charter and rules
    assert len(rows) == 1 and rows[0].detail["dropped"]["history_messages"] > 0
    assert rows[0].detail["window"] == window and "question 0" not in str(call["messages"])
    assert "question" not in str(rows[0].detail), "the audit row holds counts, never text"


def test_a_roomy_window_changes_nothing_and_writes_no_audit_row(owner_client):  # noqa: F811
    llm, rows = sent_to_the_model(200_000, turns(20, 600))
    assert rows == [] and len(llm.calls[0]["messages"]) == 41
