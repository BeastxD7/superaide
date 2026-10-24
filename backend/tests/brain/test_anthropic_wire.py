"""The Claude adapter sends exactly what Superaide sent before providers were pluggable.

`tests/fixtures/anthropic_wire.json` holds the request kwargs, the reserved worst case and the
settled usage that the original `ClaudeLLM` produced for each scenario below (recorded before
the refactor, with RECORD_WIRE=1). Every scenario runs twice: through the legacy dict interface
the tool loop still uses, and through the provider-neutral interface, which converts the same
conversation to neutral messages and back. Both must match the recording byte for byte.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from superaide.brain.llm import ClaudeLLM, SystemBlock

FIXTURE = Path(__file__).parents[1] / "fixtures" / "anthropic_wire.json"
RECORD = os.environ.get("RECORD_WIRE") == "1"


class Guard:
    """Records what the budget guard was asked, without a database."""

    def __init__(self) -> None:
        self.log: list[dict[str, Any]] = []

    def reserve(self, *, purpose: str, model: str, worst_case_usd: Decimal, **extra: Any) -> uuid.UUID:
        self.log.append({"reserve": purpose, "model": model, "worst_case_usd": str(worst_case_usd)})
        return uuid.uuid4()

    def settle(self, entry_id: uuid.UUID, *, model: str, usage: Any, request_id: str | None, **extra: Any) -> Decimal:
        self.log.append({"settle": model, "usage": vars(usage), "request_id": request_id})
        return Decimal("0.001")

    def fail(self, entry_id: uuid.UUID, error: str) -> None:
        self.log.append({"fail": error})


USAGE = SimpleNamespace(
    input_tokens=1200,
    output_tokens=80,
    cache_read_input_tokens=900,
    cache_creation_input_tokens=300,
    cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=300, ephemeral_1h_input_tokens=0),
    server_tool_use=SimpleNamespace(web_search_requests=1),
)


class Block(SimpleNamespace):
    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


class Client:
    """Stands in for anthropic.Anthropic and remembers each request."""

    def __init__(self, content: list[dict[str, Any]], stop_reason: str = "end_turn") -> None:
        self.requests: list[dict[str, Any]] = []
        self.response = SimpleNamespace(
            usage=USAGE, content=[Block(**b) for b in content], stop_reason=stop_reason, _request_id="req_wire"
        )
        self.messages = SimpleNamespace(create=self._create, stream=self._stream)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append({"call": "create", **kwargs})
        return self.response

    def _stream(self, **kwargs: Any) -> Any:
        self.requests.append({"call": "stream", **kwargs})
        client = self

        class Stream:
            current_message_snapshot = client.response

            def __enter__(self) -> Stream:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def __iter__(self):
                return iter(())

            def get_final_message(self) -> Any:
                return client.response

        return Stream()


class Answer(BaseModel):
    facts: list[str]


SYSTEM = [SystemBlock("You are Ava. Rules.", cache=True), SystemBlock("You are talking to your owner.")]
TOOLS: list[dict[str, Any]] = [
    {
        "name": "recall",
        "description": "Search the owner's notes and messages.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
    {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 3, "citations": {"enabled": True}},
    {
        "name": "propose_action",
        "description": "Ask the owner to approve something.",
        "input_schema": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["publish_post"]}, "summary": {"type": "string"}},
            "required": ["kind", "summary"],
            "additionalProperties": False,
        },
    },
]
SEARCH_RESULT = {
    "type": "search_result",
    "source": "ava://memory/123",
    "title": "Flight to BLR",
    "content": [{"type": "text", "text": "Flight to Bangalore on 3 Nov, 07:10."}],
    "citations": {"enabled": True},
}
HISTORY: list[dict[str, Any]] = [
    {"role": "user", "content": "Earlier question"},
    {"role": "assistant", "content": "[Tools used for this reply: Searching]\nEarlier answer"},
    {"role": "user", "content": "When is my flight? And the weather there?"},
    {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "Look it up.", "signature": "sig-abc=="},
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "toolu_1", "name": "recall", "input": {"query": "flight"}},
            {"type": "tool_use", "id": "toolu_2", "name": "propose_action", "input": {"kind": "x"}},
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "BLR weather"}},
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://example.com/w",
                        "title": "Weather",
                        "encrypted_content": "enc==",
                        "page_age": None,
                    }
                ],
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [SEARCH_RESULT]},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "Refused: not allowed.", "is_error": True},
        ],
    },
    {
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "Your flight is on 3 Nov.",
                "citations": [
                    {
                        "type": "search_result_location",
                        "source": "ava://memory/123",
                        "title": "Flight to BLR",
                        "cited_text": "Flight to Bangalore on 3 Nov, 07:10.",
                        "search_result_index": 0,
                        "start_block_index": 0,
                        "end_block_index": 1,
                    }
                ],
            },
            {
                "type": "text",
                "text": " It will be warm.",
                "citations": [
                    {
                        "type": "web_search_result_location",
                        "url": "https://example.com/w",
                        "title": "Weather",
                        "cited_text": "Warm",
                        "encrypted_index": "ix==",
                    }
                ],
            },
        ],
    },
    {"role": "user", "content": "Thanks. Anything else?"},
]
REPLY = [{"type": "text", "text": "Nothing else."}]
TOOL_REPLY = [
    {"type": "text", "text": "Checking."},
    {"type": "tool_use", "id": "toolu_9", "name": "recall", "input": {"query": "x"}},
]

SCENARIOS: dict[str, dict[str, Any]] = {
    "complete_plain": {
        "kind": "complete",
        "args": {"purpose": "chat_turn", "model": "claude-haiku-5-5", "max_tokens": 4000},
        "messages": [{"role": "user", "content": "hi"}],
        "reply": REPLY,
    },
    "complete_structured_effort": {
        "kind": "complete",
        "args": {"purpose": "intro_facts", "model": "claude-haiku-5-5", "max_tokens": 1500, "effort": "low"},
        "output_model": True,
        "messages": [{"role": "user", "content": "a resume"}],
        "reply": [{"type": "text", "text": '{"facts": ["Leads AI"]}'}],
    },
    "step_first_with_tools": {
        "kind": "step",
        "args": {"purpose": "chat_turn", "model": "claude-haiku-5-5", "max_tokens": 1200, "max_web_searches": 3},
        "tools": TOOLS,
        "messages": [{"role": "user", "content": "When is my flight?"}],
        "reply": TOOL_REPLY,
        "stop_reason": "tool_use",
    },
    "step_history_effort_low": {
        "kind": "step",
        "args": {
            "purpose": "chat_turn",
            "model": "claude-haiku-5-5",
            "max_tokens": 1200,
            "effort": "low",
            "max_web_searches": 3,
        },
        "tools": TOOLS,
        "messages": HISTORY,
        "reply": REPLY,
    },
    "step_streaming_history": {
        "kind": "step",
        "stream": True,
        "args": {"purpose": "chat_turn", "model": "claude-sonnet-5-5", "max_tokens": 1200, "max_web_searches": 3},
        "tools": TOOLS,
        "messages": HISTORY,
        "reply": REPLY,
        "stop_reason": "pause_turn",
    },
    "step_no_tools": {
        "kind": "step",
        "args": {"purpose": "verify", "model": "claude-haiku-5-5", "max_tokens": 600},
        "tools": [],
        "messages": [{"role": "user", "content": "Revise this."}],
        "reply": REPLY,
    },
}


def _serialisable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def run_legacy(name: str) -> dict[str, Any]:
    sc = SCENARIOS[name]
    client, guard = Client(sc["reply"], sc.get("stop_reason", "end_turn")), Guard()
    llm = ClaudeLLM(guard, client=client)  # type: ignore[arg-type]
    if sc["kind"] == "complete":
        result = llm.complete(
            system=SYSTEM, messages=sc["messages"], output_model=Answer if sc.get("output_model") else None,
            **sc["args"],
        )  # fmt: skip
        out = {"text": result.text, "stop_reason": result.stop_reason}
    else:
        events: list[Any] = []
        result = llm.step(
            system=SYSTEM, messages=sc["messages"], tools=sc["tools"],
            on_event=(lambda k, d: events.append(k)) if sc.get("stream") else None, **sc["args"],
        )  # fmt: skip
        out = {"content": result.content, "stop_reason": result.stop_reason}
    return _serialisable({"requests": client.requests, "budget": guard.log, "result": out})


def run_neutral(name: str) -> dict[str, Any]:
    """The same scenario through the provider-neutral interface."""
    from superaide.brain.providers.anthropic import AnthropicProvider, messages_from_wire, tools_from_wire

    sc = SCENARIOS[name]
    client, guard = Client(sc["reply"], sc.get("stop_reason", "end_turn")), Guard()
    provider = AnthropicProvider(guard, client=client)  # type: ignore[arg-type]
    messages = messages_from_wire(sc["messages"])
    args = dict(sc["args"])
    args.pop("max_web_searches", None)  # derived from the hosted web_search tool
    if sc["kind"] == "complete":
        provider.complete(
            system=SYSTEM, messages=messages, output_model=Answer if sc.get("output_model") else None, **args
        )
    else:
        tools = tools_from_wire(sc["tools"])
        provider.step(
            system=SYSTEM, messages=messages, tools=tools,
            on_event=(lambda e: None) if sc.get("stream") else None, **args,
        )  # fmt: skip
    return _serialisable({"requests": client.requests, "budget": [b for b in guard.log if "reserve" in b]})


def recorded() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


@pytest.mark.skipif(RECORD, reason="recording")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_legacy_interface_sends_exactly_the_recorded_request(name: str) -> None:
    assert run_legacy(name) == recorded()[name]


@pytest.mark.skipif(RECORD, reason="recording")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_neutral_interface_sends_exactly_the_recorded_request(name: str) -> None:
    want = recorded()[name]
    got = run_neutral(name)
    assert got["requests"] == want["requests"]
    assert got["budget"] == [b for b in want["budget"] if "reserve" in b]


@pytest.mark.skipif(not RECORD, reason="set RECORD_WIRE=1 to re-record (only before changing the adapter)")
def test_record() -> None:
    # The recording is the gate: it must come from the code before a refactor, never after one.
    assert not FIXTURE.exists() or os.environ.get("RECORD_WIRE_OVERWRITE") == "1", (
        "the fixture exists; delete it or set RECORD_WIRE_OVERWRITE=1 only if the old request really changed"
    )
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps({n: run_legacy(n) for n in sorted(SCENARIOS)}, indent=1, sort_keys=True) + "\n")


def test_claude_blocks_become_neutral_parts_with_citations_sources_and_taint() -> None:
    from superaide.brain.loop import search_results
    from superaide.brain.providers.anthropic import messages_from_wire, part_to_wire
    from superaide.brain.providers.fake import from_legacy
    from superaide.brain.providers.types import (
        HostedToolCall,
        HostedToolResult,
        Reasoning,
        SourceDoc,
        Text,
        ToolCall,
        ToolResult,
        tainted,
    )
    from superaide.context.recall import Source

    msgs = messages_from_wire(HISTORY)
    thinking, said, recall, propose, search, found = msgs[3].parts
    assert isinstance(thinking, Reasoning) and thinking.text == "Look it up."
    assert isinstance(said, Text) and isinstance(recall, ToolCall) and recall.args == {"query": "flight"}
    assert isinstance(search, HostedToolCall) and search.kind == "web_search"
    assert isinstance(found, HostedToolResult) and found.sources[0].id == "https://example.com/w"
    notes, refused = msgs[4].parts
    assert isinstance(notes, ToolResult) and notes.content == [
        SourceDoc("ava://memory/123", "Flight to BLR", "Flight to Bangalore on 3 Nov, 07:10.", "memory")
    ]
    assert isinstance(refused, ToolResult) and refused.is_error and refused.content == "Refused: not allowed."
    first, second = msgs[5].parts
    assert isinstance(first, Text) and first.citations[0].source_id == "ava://memory/123"
    assert first.citations[0].kind == "memory" and first.citations[0].cited_text.startswith("Flight")
    assert isinstance(second, Text) and second.citations[0].kind == "web"
    assert tainted(msgs[3].parts) and not tainted(msgs[4].parts)
    assert from_legacy(HISTORY[3]["content"]) == msgs[3].parts

    # Built from neutral fields alone (no raw block), a source becomes the loop's own search_result.
    fresh = ToolResult(
        "toolu_1", [SourceDoc("ava://memory/123", "Flight to BLR", "Flight to Bangalore on 3 Nov, 07:10.")]
    )
    when = datetime(2026, 10, 21, tzinfo=UTC)
    loop_shape = search_results(
        [Source("ava://memory/123", "memory", "Flight to BLR", "Flight to Bangalore on 3 Nov, 07:10.", when)]
    )
    assert part_to_wire(fresh) == {"type": "tool_result", "tool_use_id": "toolu_1", "content": loop_shape}


def test_the_neutral_stream_names_no_claude_block_types() -> None:
    from superaide.brain.providers.anthropic import AnthropicProvider, messages_from_wire, tools_from_wire
    from superaide.brain.providers.types import (
        HostedToolFinished,
        HostedToolStarted,
        PartDone,
        ReasoningDelta,
        TextDelta,
    )

    blocks = HISTORY[3]["content"]
    client = Client(blocks, "pause_turn")

    def stream(**kwargs: Any) -> Any:
        snapshot = client.response

        class S:
            current_message_snapshot = snapshot

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def __iter__(self):
                delta = SimpleNamespace(type="thinking_delta", thinking="hm")
                yield SimpleNamespace(type="content_block_delta", delta=delta)
                yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="Let"))
                for i in range(len(blocks)):
                    yield SimpleNamespace(type="content_block_stop", index=i)

            def get_final_message(self):
                return snapshot

        return S()

    client.messages.stream = stream
    events: list[Any] = []
    step = AnthropicProvider(Guard(), client=client).step(
        purpose="chat_turn", model="claude-haiku-5-5", system=SYSTEM, messages=messages_from_wire(HISTORY[:3]),
        tools=tools_from_wire(TOOLS), max_tokens=100, on_event=events.append,
    )  # fmt: skip
    assert step.stop == "pause" and step.raw_stop == "pause_turn" and step.usage.hosted_tool_calls == {"web_search": 1}
    assert isinstance(events[0], ReasoningDelta) and isinstance(events[1], TextDelta)
    assert [type(e).__name__ for e in events if isinstance(e, HostedToolStarted | HostedToolFinished)] == [
        "HostedToolStarted", "HostedToolFinished",
    ]  # fmt: skip
    assert sum(isinstance(e, PartDone) for e in events) == len(blocks)
