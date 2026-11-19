"""OpenAI-compatible providers (ADR 0007 WP6), driven through the real `openai` SDK against a
mock HTTP transport: request shape per preset, tool calls, streaming, citation markers, usage
and cost, structured output, connection checks, and the tool loop running on top."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import openai
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from superaide.brain.citations import extract
from superaide.brain.llm import ProviderLLM
from superaide.brain.models import SpendEntry
from superaide.brain.providers.openai_compat import OpenAICompatProvider, strict_schema
from superaide.brain.providers.presets import PRESETS
from superaide.brain.providers.types import (
    Message,
    PartDone,
    SourceDoc,
    SystemBlock,
    Text,
    TextDelta,
    ToolCall,
    ToolCallReady,
    ToolResult,
    ToolSpec,
)
from superaide.brain.spend.budget import BudgetGuard
from superaide.brain.spend.pricing import ModelPrice, PriceRegistry, UnknownModelPrice
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from tests.pipeline.test_loop import note, owner_client, turn_for  # noqa: F401  (owner_client is a fixture)

PRICE = ModelPrice(Decimal("1"), Decimal("4"))  # US$ per million tokens
NOTE = SourceDoc("ava://memory/12", "Flight to BLR", "Flight to Bangalore on 3 Nov, 07:10.", "memory")
RECALL = ToolSpec("recall", "Search notes", {"type": "object", "properties": {"query": {"type": "string"}}})


class Server:
    """A scripted OpenAI-compatible server: records requests, plays responses in order."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        if request.method == "GET":
            return self.responses.pop(0)
        self.requests.append(json.loads(request.content))
        out = self.responses.pop(0)
        if isinstance(out, httpx.Response):
            return out
        if isinstance(out, list):  # a stream of chunks
            body = "".join(f"data: {json.dumps(c)}\n\n" for c in out) + "data: [DONE]\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=out)


def completion(content: str | None = None, calls: list | None = None, finish: str = "stop", usage: Any = "default"):
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for cid, name, args in calls
        ]
    body: dict[str, Any] = {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }  # fmt: skip
    if usage == "default":
        usage = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}
    if usage is not None:
        body["usage"] = usage
    return body


def provider(server: Server, preset: str = "openai", prices: PriceRegistry | None = None, **kw: Any):
    p = PRESETS[preset]
    base = kw.pop("base_url", p.base_url or "https://example.openai.azure.com/openai/v1/")
    client = openai.OpenAI(
        api_key="sk-test", base_url=base, http_client=httpx.Client(transport=httpx.MockTransport(server)),
        max_retries=0,
    )  # fmt: skip
    guard = BudgetGuard(10, prices=prices or PriceRegistry({(preset, "m"): PRICE}))
    return OpenAICompatProvider(guard, p, api_key="sk-test", base_url=base, client=client, **kw), guard


def ask(text: str = "When is my flight?") -> list[Message]:
    return [Message("user", [Text(text)])]


def test_a_tool_call_step_sends_the_chat_completions_shape_and_bounds_reasoning():
    server = Server(completion(None, [("call_1", "recall", {"query": "flight"})], "tool_calls"))
    p, guard = provider(server)
    step = p.step(
        purpose="chat_turn", model="m", system=[SystemBlock("You are Ava.", cache=True)], messages=ask(),
        tools=[RECALL], max_tokens=1200, effort="low",
    )  # fmt: skip
    [req] = server.requests
    assert req["model"] == "m" and req["max_completion_tokens"] == 1200 and "max_tokens" not in req
    assert req["reasoning_effort"] == "low"
    assert req["tools"] == [{"type": "function", "function": {"name": "recall", "description": "Search notes",
                                                              "parameters": RECALL.input_schema}}]  # fmt: skip
    assert req["messages"][0]["role"] == "system" and "[ava://memory/12]" in req["messages"][0]["content"]
    assert req["messages"][1] == {"role": "user", "content": "When is my flight?"}
    assert step.stop == "tool_calls" and step.parts == [ToolCall("call_1", "recall", {"query": "flight"})]
    # 1000 in at $1/M + 100 out at $4/M
    assert step.cost_usd == Decimal("0.0014") and guard.summary().settled_usd == Decimal("0.0014")
    with session_scope() as s:
        row = s.scalar(select(SpendEntry))
        assert row is not None and row.provider == "openai" and row.model == "m"


def test_sources_go_out_labelled_and_only_real_ones_come_back_as_citations():
    answer = "Your flight is on 3 Nov [ava://memory/12]. You also booked a hotel [ava://memory/99]."
    server = Server(completion(answer))
    p, _ = provider(server)
    history = [
        *ask(),
        Message("assistant", [ToolCall("call_1", "recall", {"query": "flight"})]),
        Message("user", [ToolResult("call_1", [NOTE])]),
    ]
    step = p.step(purpose="chat_turn", model="m", system=[], messages=history, tools=[RECALL], max_tokens=500)
    msgs = server.requests[0]["messages"]
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "recall", "arguments": '{"query": "flight"}'}
    assert msgs[3]["role"] == "tool" and msgs[3]["tool_call_id"] == "call_1"
    assert '[source id=ava://memory/12 title="Flight to BLR"]' in msgs[3]["content"]
    texts = [x for x in step.parts if isinstance(x, Text)]
    assert "".join(t.text for t in texts) == "Your flight is on 3 Nov. You also booked a hotel."
    assert [c.source_id for t in texts for c in t.citations] == ["ava://memory/12"]  # the invented id is gone
    assert texts[0].citations[0].kind == "memory" and texts[0].citations[0].title == "Flight to BLR"


def test_citation_markers_split_claims_and_merge_repeated_marks():
    web = SourceDoc("https://example.com/w", "Weather", "Warm", "web")
    parts, dropped = extract("A [ava://memory/12][https://example.com/w]. B [ava://x/1]. C", [NOTE, web])
    assert [p.text for p in parts] == ["A", ". B. C"]
    assert [c.source_id for c in parts[0].citations] == ["ava://memory/12", "https://example.com/w"]
    assert dropped == ["ava://x/1"]


def test_a_stream_rebuilds_tool_calls_reports_deltas_and_settles_from_the_usage_chunk():
    def chunk(delta: dict | None, finish: str | None = None, usage: dict | None = None) -> dict:
        choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
        c: dict[str, Any] = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": choices,
        }
        if usage:
            c["usage"] = usage
        return c

    call = {"index": 0, "id": "call_9", "type": "function", "function": {"name": "recall", "arguments": '{"que'}}
    server = Server([
        chunk({"role": "assistant", "content": "Let me "}),
        chunk({"content": "check."}),
        chunk({"tool_calls": [call]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'ry": "x"}'}}]}, "tool_calls"),
        chunk(None, usage={"prompt_tokens": 2000, "completion_tokens": 50, "total_tokens": 2050}),
    ])  # fmt: skip
    p, guard = provider(server)
    events: list[Any] = []
    step = p.step(purpose="chat_turn", model="m", system=[], messages=ask(), tools=[RECALL], max_tokens=300,
                  on_event=events.append)  # fmt: skip
    assert server.requests[0]["stream"] is True and server.requests[0]["stream_options"] == {"include_usage": True}
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Let me check."
    assert any(isinstance(e, ToolCallReady) for e in events) and any(isinstance(e, PartDone) for e in events)
    assert step.stop == "tool_calls" and step.parts[-1] == ToolCall("call_9", "recall", {"query": "x"})
    assert step.cost_usd == Decimal("0.0022") and guard.summary().settled_usd == Decimal("0.0022")


def test_a_response_without_usage_is_charged_its_full_reservation():
    server = Server(completion("hi", usage=None))
    p, guard = provider(server)
    step = p.step(purpose="chat_turn", model="m", system=[], messages=ask(), tools=[], max_tokens=1000)
    assert step.usage.known is False and step.cost_usd > Decimal("0.004")  # at least 1000 output tokens at $4/M
    assert guard.summary().settled_usd == step.cost_usd
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "usage_missing")) is not None


def test_openrouter_reports_its_charge_and_the_higher_figure_counts():
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100, "cost": 0.002}
    server = Server(completion("hi", usage=usage))
    p, _ = provider(server, "openrouter")
    step = p.step(purpose="chat_turn", model="m", system=[], messages=ask(), tools=[], max_tokens=100)
    assert step.usage.reported_cost_usd == Decimal("0.002") and step.cost_usd == Decimal("0.002")


def test_an_unpriced_model_is_refused_before_any_request():
    server = Server()
    p, _ = provider(server, prices=PriceRegistry())
    with pytest.raises(UnknownModelPrice):
        p.step(purpose="chat_turn", model="gpt-unknown", system=[], messages=ask(), tools=[], max_tokens=100)
    assert server.requests == []


def test_a_local_ollama_needs_no_key_costs_nothing_and_gets_max_tokens():
    server = Server(completion("hello"))
    base = "http://localhost:11434/v1"
    p, guard = provider(server, "ollama", prices=PriceRegistry(base_urls={"ollama": base}), base_url=base)
    step = p.step(purpose="chat_turn", model="llama3.2", system=[], messages=ask(), tools=[RECALL], max_tokens=100)
    req = server.requests[0]
    assert req["max_tokens"] == 100 and "max_completion_tokens" not in req and req["parallel_tool_calls"] is False
    assert step.cost_usd == 0 and guard.summary().calls == 1
    # The same model on a public address isn't free.
    public = Server(completion("hello"))
    remote, _ = provider(public, "ollama", prices=PriceRegistry(base_urls={"ollama": "https://8.8.8.8/v1"}),
                         base_url="https://8.8.8.8/v1")  # fmt: skip
    with pytest.raises(UnknownModelPrice):
        remote.step(purpose="chat_turn", model="llama3.2", system=[], messages=ask(), tools=[], max_tokens=10)


def test_azure_also_sends_the_key_as_api_key():
    server = Server(completion("hi"))
    p, _ = provider(server, "azure", base_url="https://res.openai.azure.com/openai/v1/")
    p.client = openai.OpenAI(
        api_key="sk-test", base_url=p.base_url, default_headers={"api-key": "sk-test"},
        http_client=httpx.Client(transport=httpx.MockTransport(server)),
    )  # fmt: skip
    p.guard.prices = PriceRegistry({("azure", "m"): PRICE})  # type: ignore[attr-defined]
    p.step(purpose="chat_turn", model="m", system=[], messages=ask(), tools=[], max_tokens=10)
    assert server.headers[0]["api-key"] == "sk-test"


class Facts(BaseModel):
    facts: list[str]
    note: str | None = None


def test_structured_output_uses_a_strict_schema_and_retries_once_with_the_error():
    server = Server(completion('{"wrong": 1}'), completion('{"facts": ["Leads AI"], "note": null}'))
    p, guard = provider(server)
    done = p.complete(purpose="intro_facts", model="m", system=[], messages=ask("a resume"), max_tokens=300,
                      output_model=Facts)  # fmt: skip
    fmt = server.requests[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    assert sorted(fmt["json_schema"]["schema"]["required"]) == ["facts", "note"]
    assert "That wasn't valid" in server.requests[1]["messages"][-1]["content"]
    assert done.parsed == Facts(facts=["Leads AI"]) and guard.summary().calls == 2


def test_strict_schema_closes_nested_objects_and_makes_optional_fields_nullable():
    schema = strict_schema(
        {
            "type": "object",
            "properties": {
                "a": {"type": "object", "properties": {"b": {"type": "string"}}},
                "c": {"type": "integer", "default": 1},
            },
            "required": ["a"],
        }  # fmt: skip
    )
    assert schema["required"] == ["a", "c"] and schema["properties"]["c"] == {
        "anyOf": [{"type": "integer"}, {"type": "null"}]
    }
    assert schema["properties"]["a"]["additionalProperties"] is False and schema["properties"]["a"]["required"] == ["b"]


@pytest.mark.parametrize(
    ("status", "words"),
    [(401, "didn't accept that key"), (404, "Check the base URL"), (500, "Couldn't reach")],
)
def test_check_explains_what_went_wrong_in_plain_words(status, words):
    p, _ = provider(Server(httpx.Response(status, json={"error": {"message": "no"}})))
    report = p.check()
    assert not report.ok and words in report.message and "sk-test" not in report.message


def test_check_lists_the_models():
    p, _ = provider(Server(httpx.Response(200, json={"object": "list", "data": [
        {"id": "m2", "object": "model", "created": 1, "owned_by": "x"},
        {"id": "m1", "object": "model", "created": 1, "owned_by": "x"},
    ]})))  # fmt: skip
    report = p.check()
    assert report.ok and report.models == ["m1", "m2"]


@pytest.mark.usefixtures("owner_client")
def test_the_owner_tool_loop_runs_on_an_openai_compatible_model_with_checked_citations():
    from superaide.app.accounts import owner as hired_owner

    with session_scope() as s:
        person = hired_owner(s)
        assert person is not None
        m = note(s, person.id, "My flight to Bangalore is on Friday at 7am")
        server = Server(
            completion(None, [("call_1", "recall", {"query": "flight Bangalore"})], "tool_calls"),
            completion(f"Friday at 7am [ava://memory/{m.id}]. And a hotel [ava://memory/999]."),
        )
        p, _ = provider(server, prices=PriceRegistry({("openai", "claude-haiku-5-5"): PRICE}))
        turn = turn_for(s, lambda *a: None)
        turn.llm = ProviderLLM(p)
        result = turn.run([{"role": "user", "content": "When is my flight?"}])
    assert "[source id=ava://memory/" in server.requests[1]["messages"][-1]["content"]
    assert [c.uri for c in result.sources] == [f"ava://memory/{m.id}"]
    assert result.text.startswith("Friday at 7am [1].") and "999" not in result.text


@pytest.mark.parametrize(
    "chunks",
    [
        ["Your flight is on 3 Nov [ava://memory/12]. Pack light."],
        ["Your flight is on 3 Nov [", "ava://mem", "ory/12]. Pack light."],
        ["Your flight is on 3 Nov ", "[ava://memory/12", "]", ". Pack light."],
        ["Your flight is on 3 Nov [ava://memory/12][https://ex", "ample.com/a]. Pack light."],
    ],
)
def test_streamed_text_never_shows_citation_markers(chunks):
    from superaide.brain.citations import MarkerFilter

    f = MarkerFilter()
    shown = "".join(f.feed(c) for c in chunks) + f.flush()
    assert shown == "Your flight is on 3 Nov. Pack light."


def test_brackets_that_are_not_markers_stream_through():
    from superaide.brain.citations import MarkerFilter

    f = MarkerFilter()
    shown = "".join(f.feed(c) for c in ["Options [a", "] and [see note]", " and [1]."]) + f.flush()
    assert shown == "Options [a] and [see note] and [1]."


# --- reasoning never reaches a reply (any provider) -------------------------------------------


def _chunk(delta: dict | None, finish: str | None = None, usage: dict | None = None) -> dict:
    choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
    c: dict[str, Any] = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": choices}
    if usage:
        c["usage"] = usage
    return c


USAGE = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
THOUGHT = "Okay, the user said hi. Let me check the available functions."


def _streamed_reply(*chunks: dict) -> tuple[str, list[Any]]:
    server = Server(list(chunks))
    p, _ = provider(server, "ollama", prices=PriceRegistry(base_urls={"ollama": "http://localhost:11434/v1"}),
                    base_url="http://localhost:11434/v1")  # fmt: skip
    events: list[Any] = []
    turn = turn_for_stream(p, events)
    return turn, events


def turn_for_stream(p: OpenAICompatProvider, events: list[Any]) -> str:
    from superaide.brain.llm import ProviderLLM

    result = ProviderLLM(p).step(
        purpose="chat_turn", model="qwen3:8b", system=[], messages=[{"role": "user", "content": "hi"}], tools=[],
        max_tokens=100, on_event=lambda kind, data: events.append((kind, data)),
    )  # fmt: skip
    from superaide.brain.loop import render

    return render(result.content)[0]


def test_ollama_turns_thinking_off_by_default():
    server = Server(completion("hello"))
    base = "http://localhost:11434/v1"
    p, _ = provider(server, "ollama", prices=PriceRegistry(base_urls={"ollama": base}), base_url=base)
    p.step(purpose="chat_turn", model="qwen3:8b", system=[], messages=ask(), tools=[], max_tokens=100)
    assert server.requests[0]["reasoning_effort"] == "none"
    other = Server(completion("hello"))
    q, _ = provider(other, "vllm", prices=PriceRegistry(base_urls={"vllm": "http://localhost:8000/v1"}),
                    base_url="http://localhost:8000/v1")  # fmt: skip
    q.step(purpose="chat_turn", model="m", system=[], messages=ask(), tools=[], max_tokens=100)
    assert "reasoning_effort" not in other.requests[0]


def test_streamed_reasoning_deltas_are_shown_as_thinking_but_never_join_the_reply():
    text, events = _streamed_reply(
        _chunk({"role": "assistant", "reasoning": THOUGHT[:20]}),
        _chunk({"reasoning": THOUGHT[20:]}),
        _chunk({"content": "Hi! How can I help?"}, "stop"),
        _chunk(None, usage=USAGE),
    )  # fmt: skip
    assert text == "Hi! How can I help?" and THOUGHT[:10] not in text
    assert "".join(d["delta"] for k, d in events if k == "thinking") == THOUGHT
    assert not any(THOUGHT[:10] in d.get("delta", "") for k, d in events if k == "text")
    assert not any(k == "block" and THOUGHT[:10] in json.dumps(d) for k, d in events)


def test_reasoning_only_reply_is_empty_not_the_reasoning():
    text, _ = _streamed_reply(_chunk({"reasoning_content": THOUGHT}, "length"), _chunk(None, usage=USAGE))
    assert text == ""


def test_reasoning_on_a_tool_call_turn_never_becomes_the_answer():
    call = {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "recall", "arguments": '{"query": "x"}'},
    }
    server = Server(
        [_chunk({"reasoning": THOUGHT}), _chunk({"tool_calls": [call]}, "tool_calls"), _chunk(None, usage=USAGE)],
        [_chunk({"reasoning": "Now answer."}), _chunk({"content": "Friday."}, "stop"), _chunk(None, usage=USAGE)],
    )
    p, _ = provider(server, "ollama", prices=PriceRegistry(base_urls={"ollama": "http://localhost:11434/v1"}),
                    base_url="http://localhost:11434/v1")  # fmt: skip
    from superaide.brain.llm import ProviderLLM
    from superaide.brain.loop import render

    first = ProviderLLM(p).step(
        purpose="chat_turn", model="qwen3:8b", system=[], messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "recall", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
        max_tokens=100, on_event=lambda *_: None,
    )  # fmt: skip
    assert any(b["type"] == "tool_use" for b in first.content)
    assert render(first.content)[0] == ""
    assert not any(THOUGHT[:10] in json.dumps(b) for b in first.content)


def test_a_non_streamed_message_keeps_reasoning_out_of_the_visible_text():
    server = Server(
        httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "reasoning": THOUGHT, "content": "Hi there."}}],
            "usage": USAGE,
        })
    )  # fmt: skip
    p, _ = provider(server, "ollama", prices=PriceRegistry(base_urls={"ollama": "http://localhost:11434/v1"}),
                    base_url="http://localhost:11434/v1")  # fmt: skip
    from superaide.brain.llm import ProviderLLM
    from superaide.brain.loop import render

    step = ProviderLLM(p).step(purpose="chat_turn", model="qwen3:8b", system=[],
                               messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=100)  # fmt: skip
    assert render(step.content)[0] == "Hi there."


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<think>Let me think.</think>Hello!", "Hello!"),
        ("<think>\nstep 1\nstep 2\n</think>\n\nHello!", "Hello!"),
        ("Hello <THINK>secret</THINK>there", "Hello there"),
        ("<think>one</think>A<think>two</think>B", "AB"),
        ("Done.<think>cut off by the token limit", "Done."),
        ("reasoning with no opening tag</think>The answer.", "The answer."),
        ("No tags, even <b>html</b> and a < b.", "No tags, even <b>html</b> and a < b."),
    ],
)
def test_strip_think_removes_every_think_block(raw, clean):
    from superaide.brain.providers.thinking import strip_think

    assert strip_think(raw) == clean


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 100])
@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<think>Let me think.</think>Hello there!", "Hello there!"),
        ("Hello <think>hidden</think>there, a < b.", "Hello there, a < b."),
        ("Hi <thing> ok", "Hi <thing> ok"),
    ],
)
def test_streamed_think_blocks_never_show_however_the_chunks_fall(raw, clean, size):
    from superaide.brain.providers.thinking import ThinkFilter

    f = ThinkFilter()
    shown = "".join(f.feed(raw[i : i + size]) for i in range(0, len(raw), size)) + f.flush()
    assert shown.strip() == clean


def test_think_tags_in_content_never_reach_the_streamed_or_final_reply():
    text, events = _streamed_reply(
        _chunk({"content": "<thi"}), _chunk({"content": "nk>Okay, the user said hi.</th"}),
        _chunk({"content": "ink>Hello!"}, "stop"), _chunk(None, usage=USAGE),
    )  # fmt: skip
    assert text == "Hello!"
    assert "".join(d["delta"] for k, d in events if k == "text") == "Hello!"


def test_think_blocks_in_any_providers_text_block_are_dropped_by_the_loop():
    from superaide.brain.loop import render

    assert render([{"type": "text", "text": "<think>plan</think>Answer."}])[0] == "Answer."
    assert render([{"type": "text", "text": "<think>only thoughts</think>"}])[0] == ""
