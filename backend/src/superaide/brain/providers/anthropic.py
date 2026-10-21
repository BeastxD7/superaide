"""Claude through the Anthropic Messages API, with every feature kept: search_result citations,
server tools (web search and fetch, with pause_turn), adaptive thinking while streaming,
prompt caching, effort and structured output.

Two interfaces share one request builder and one spend path:
- `step_wire` / `complete_wire` take and return Messages API dicts (what brain/llm.py's
  `ClaudeLLM` and the tool loop use today);
- `step` / `complete` take neutral `Message`s and return neutral parts. Each part keeps its
  original block in `provider_data["anthropic"]`, which is sent back unchanged, so a round trip
  through the neutral model produces the same request byte for byte
  (tests/test_anthropic_wire.py).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from superaide.brain.pricing import WEB_SEARCH_USD, UsageCounts, worst_case_cost
from superaide.brain.providers.base import CheckReport, Guard, metered
from superaide.brain.providers.types import (
    Capabilities,
    Citation,
    Completion,
    ContentPart,
    Effort,
    HostedTool,
    HostedToolCall,
    HostedToolFinished,
    HostedToolResult,
    HostedToolStarted,
    Message,
    OnStreamEvent,
    PartDone,
    Reasoning,
    ReasoningDelta,
    SourceDoc,
    Step,
    StopReason,
    SystemBlock,
    Text,
    TextDelta,
    Tool,
    ToolCall,
    ToolCallReady,
    ToolResult,
    ToolSpec,
    Usage,
    source_kind,
)

log = logging.getLogger(__name__)
KEY = "anthropic"  # the provider_data key for this adapter's raw blocks

# Live progress in the wire shape: ("text", {"delta"}), ("thinking", {"delta"}), ("block", {"block"}).
WireEvent = Callable[[str, dict[str, Any]], None]

HOSTED_TYPES = {"web_search": "web_search_20250305", "web_fetch": "web_fetch_20250910"}
HOSTED_RESULTS = {"web_search_tool_result": "web_search", "web_fetch_tool_result": "web_fetch"}
STOPS: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_calls",
    "pause_turn": "pause",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "refusal": "refusal",
}
CAPABILITIES = Capabilities(
    tools=True,
    parallel_tools=True,
    streaming=True,
    reasoning=True,
    native_citations=True,
    server_web_search=True,
    server_web_fetch=True,
    structured_output="json_schema",
    prompt_caching="explicit",
    vision=True,
    max_output_tokens_bounds_reasoning=True,
)

# --- the request ------------------------------------------------------------------


def prompt_chars(
    system: list[SystemBlock], messages: list[dict[str, Any]], output_model: type[BaseModel] | None
) -> int:
    chars = sum(len(b.text) for b in system) + len(json.dumps(messages, default=str))
    if output_model is not None:
        chars += len(json.dumps(output_model.model_json_schema()))
    return chars


def system_param(system: list[SystemBlock]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in system:
        item: dict[str, Any] = {"type": "text", "text": block.text}
        if block.cache:
            item["cache_control"] = {"type": "ephemeral"}
        out.append(item)
    return out


def block_dict(block: Any) -> dict[str, Any]:
    return block.model_dump(exclude_none=True) if hasattr(block, "model_dump") else dict(block)


def step_kwargs(
    *,
    model: str,
    system: list[SystemBlock],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
    effort: str | None,
    streaming: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_param(system),
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    if effort:
        kwargs["output_config"] = {"effort": effort}
    if streaming:
        # Streamed, with Claude's reasoning summarised so the owner can watch it work.
        kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
    return kwargs


def complete_kwargs(
    *,
    model: str,
    system: list[SystemBlock],
    messages: list[dict[str, Any]],
    max_tokens: int,
    output_model: type[BaseModel] | None,
    effort: str | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_param(system),
        "messages": messages,
    }
    output_config: dict[str, Any] = {}
    if effort:
        output_config["effort"] = effort
    if output_model is not None:
        from anthropic import transform_schema

        output_config["format"] = {"type": "json_schema", "schema": transform_schema(output_model.model_json_schema())}
    if output_config:
        kwargs["output_config"] = output_config
    return kwargs


# --- neutral <-> wire ---------------------------------------------------------------


def citation_from_wire(c: dict[str, Any]) -> Citation | None:
    kind = c.get("type")
    if kind == "search_result_location":
        uri = str(c.get("source", ""))
        return Citation(uri, str(c.get("title") or ""), str(c.get("cited_text") or ""), source_kind(uri))
    if kind == "web_search_result_location" or (kind == "char_location" and c.get("url")):
        return Citation(str(c.get("url", "")), str(c.get("title") or ""), str(c.get("cited_text") or ""), "web")
    return None


def source_from_wire(block: dict[str, Any]) -> SourceDoc:
    uri = str(block.get("source", ""))
    text = "".join(str(t.get("text", "")) for t in block.get("content") or [] if t.get("type") == "text")
    return SourceDoc(uri, str(block.get("title") or ""), text, source_kind(uri))


def source_to_wire(src: SourceDoc) -> dict[str, Any]:
    return {
        "type": "search_result",
        "source": src.id,
        "title": src.title,
        "content": [{"type": "text", "text": src.text}],
        "citations": {"enabled": True},
    }


def part_from_wire(block: dict[str, Any]) -> ContentPart:
    raw = {KEY: block}
    kind = block.get("type")
    if kind == "text":
        cites = [c for c in (citation_from_wire(x) for x in block.get("citations") or []) if c is not None]
        return Text(str(block.get("text", "")), cites, raw)
    if kind in {"thinking", "redacted_thinking"}:
        return Reasoning(str(block.get("thinking", "")), raw)
    if kind == "tool_use":
        return ToolCall(str(block.get("id", "")), str(block.get("name", "")), dict(block.get("input") or {}), raw)
    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, list) and all(isinstance(b, dict) and b.get("type") == "search_result" for b in content):
            body: str | list[SourceDoc] = [source_from_wire(b) for b in content]
        elif isinstance(content, list):
            body = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        else:
            body = str(content or "")
        return ToolResult(str(block.get("tool_use_id", "")), body, bool(block.get("is_error")), raw)
    if kind == "server_tool_use":
        return HostedToolCall(str(block.get("id", "")), str(block.get("name", "")), dict(block.get("input") or {}), raw)
    if kind in HOSTED_RESULTS:
        found = block.get("content")
        error = str(found.get("error_code")) if isinstance(found, dict) and "error_code" in found else None
        sources = [
            SourceDoc(str(r.get("url", "")), str(r.get("title") or ""), "", "web")
            for r in (found if isinstance(found, list) else [])
            if isinstance(r, dict)
        ]
        return HostedToolResult(str(block.get("tool_use_id", "")), HOSTED_RESULTS[kind], sources, error, raw)
    # A block type this adapter doesn't model yet: carried through untouched for Claude, empty elsewhere.
    return Text("", [], raw)


def part_to_wire(part: ContentPart) -> dict[str, Any]:
    if part.provider_data and KEY in part.provider_data:
        return part.provider_data[KEY]
    if isinstance(part, Text):
        return {"type": "text", "text": part.text}
    if isinstance(part, ToolCall):
        return {"type": "tool_use", "id": part.id, "name": part.name, "input": part.args}
    if isinstance(part, ToolResult):
        content: Any = part.content if isinstance(part.content, str) else [source_to_wire(s) for s in part.content]
        out: dict[str, Any] = {"type": "tool_result", "tool_use_id": part.call_id, "content": content}
        if part.is_error:
            out["is_error"] = True
        return out
    if isinstance(part, Reasoning):
        # Thinking from another provider has no signature Claude would accept: say it as text.
        return {"type": "text", "text": part.text}
    raise ValueError(f"{type(part).__name__} from another provider can't be replayed to Claude")


def messages_from_wire(messages: list[dict[str, Any]]) -> list[Message]:
    out: list[Message] = []
    for m in messages:
        content = m["content"]
        parts: list[ContentPart] = (
            [Text(content)] if isinstance(content, str) else [part_from_wire(dict(b)) for b in content]
        )
        out.append(Message(m["role"], parts))
    return out


def messages_to_wire(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        only = m.parts[0] if len(m.parts) == 1 else None
        if isinstance(only, Text) and not only.provider_data and not only.citations:
            out.append({"role": m.role, "content": only.text})  # plain turns stay plain strings
        else:
            out.append({"role": m.role, "content": [part_to_wire(p) for p in m.parts]})
    return out


def tools_from_wire(tools: list[dict[str, Any]]) -> list[Tool]:
    out: list[Tool] = []
    for t in tools:
        if "type" in t and t.get("name") in HOSTED_TYPES:
            out.append(HostedTool(t["name"], int(t.get("max_uses", 3))))
        else:
            out.append(ToolSpec(t["name"], t.get("description", ""), t["input_schema"]))
    return out


def tools_to_wire(tools: list[Tool]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools:
        if isinstance(t, HostedTool):
            spec: dict[str, Any] = {"type": HOSTED_TYPES[t.kind], "name": t.kind, "max_uses": t.max_uses}
            if t.kind == "web_fetch":
                spec["citations"] = {"enabled": True}
            out.append(spec)
        else:
            out.append({"name": t.name, "description": t.description, "input_schema": t.input_schema})
    return out


def usage_from_counts(u: UsageCounts) -> Usage:
    return Usage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
        cache_write_tokens=u.cache_write_5m_tokens + u.cache_write_1h_tokens,
        hosted_tool_calls={"web_search": u.web_search_requests} if u.web_search_requests else {},
        extras={"cache_write_5m_tokens": u.cache_write_5m_tokens, "cache_write_1h_tokens": u.cache_write_1h_tokens},
    )


def _neutral_events(on_event: OnStreamEvent) -> WireEvent:
    def emit(kind: str, data: dict[str, Any]) -> None:
        if kind == "text":
            on_event(TextDelta(str(data.get("delta", ""))))
        elif kind == "thinking":
            on_event(ReasoningDelta(str(data.get("delta", ""))))
        elif kind == "block":
            part = part_from_wire(dict(data.get("block") or {}))
            if isinstance(part, ToolCall):
                on_event(ToolCallReady(part))
            elif isinstance(part, HostedToolCall):
                on_event(HostedToolStarted(part))
            elif isinstance(part, HostedToolResult):
                on_event(HostedToolFinished(part))
            on_event(PartDone(part))

    return emit


# --- the provider ---------------------------------------------------------------------


class AnthropicProvider:
    kind = "anthropic"

    def __init__(self, guard: Guard, api_key: str = "", client: Any = None, *, id: str = "anthropic") -> None:
        if client is None:
            import anthropic

            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set; add it to .env or set FAKE_LLM=true")
            client = anthropic.Anthropic(api_key=api_key, max_retries=2)
        self.client = client
        self.guard = guard
        self.id = id

    def capabilities(self, model: str) -> Capabilities:
        return CAPABILITIES

    def check(self) -> CheckReport:
        try:
            page = self.client.models.list(limit=20)
        except Exception as exc:
            return CheckReport(False, f"Anthropic didn't answer: {type(exc).__name__}")
        return CheckReport(True, "Connected", [m.id for m in getattr(page, "data", [])])

    # --- wire interface (the tool loop uses this until it speaks neutral parts) ---

    def _metered(
        self, purpose: str, model: str, worst: Decimal, call: Callable[[], Any]
    ) -> tuple[Any, UsageCounts, Decimal]:
        return metered(
            self.guard, purpose=purpose, model=model, worst_case_usd=worst, call=call,
            priced=lambda r: (UsageCounts.from_api(r.usage), getattr(r, "_request_id", None)),
        )  # fmt: skip

    def step_wire(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        effort: str | None = None,
        max_web_searches: int = 0,
        on_event: WireEvent | None = None,
    ) -> tuple[list[dict[str, Any]], str, UsageCounts, Decimal]:
        """Returns (content blocks as dicts, stop_reason, usage, cost)."""
        prompt = prompt_chars(system, messages, None) + len(json.dumps(tools))
        worst = worst_case_cost(model, prompt, max_tokens) + Decimal(max_web_searches) * WEB_SEARCH_USD
        kwargs = step_kwargs(
            model=model, system=system, messages=messages, tools=tools, max_tokens=max_tokens, effort=effort,
            streaming=on_event is not None,
        )  # fmt: skip
        if on_event is None:
            call: Callable[[], Any] = lambda: self.client.messages.create(**kwargs)  # noqa: E731
        else:
            call = lambda: self._stream(kwargs, on_event)  # noqa: E731
        response, usage, cost = self._metered(purpose, model, worst, call)
        content = [block_dict(b) for b in response.content]
        return content, response.stop_reason or "end_turn", usage, cost

    def _stream(self, kwargs: dict[str, Any], on_event: WireEvent) -> Any:
        with self.client.messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        on_event("text", {"delta": event.delta.text})
                    elif event.delta.type == "thinking_delta":
                        on_event("thinking", {"delta": event.delta.thinking})
                elif event.type == "content_block_stop":
                    on_event("block", {"block": block_dict(stream.current_message_snapshot.content[event.index])})
            return stream.get_final_message()

    def complete_wire(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: str | None = None,
    ) -> tuple[str, Any, UsageCounts, Decimal, str | None]:
        """Returns (text, parsed, usage, cost, stop_reason)."""
        worst = worst_case_cost(model, prompt_chars(system, messages, output_model), max_tokens)
        kwargs = complete_kwargs(
            model=model, system=system, messages=messages, max_tokens=max_tokens, output_model=output_model,
            effort=effort,
        )  # fmt: skip
        response, usage, cost = self._metered(purpose, model, worst, lambda: self.client.messages.create(**kwargs))
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        stop_reason = getattr(response, "stop_reason", None)
        parsed = None
        if output_model is not None:
            if stop_reason == "max_tokens":
                log.warning("%s: output cut off at max_tokens=%s; nothing parsed", purpose, max_tokens)
            else:
                try:
                    parsed = output_model.model_validate_json(text)
                except ValidationError as exc:
                    log.warning(
                        "%s: output did not match %s (stop_reason=%s): %s",
                        purpose, output_model.__name__, stop_reason, exc,
                    )  # fmt: skip
        return text, parsed, usage, cost, stop_reason

    # --- neutral interface ---

    def step(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int,
        effort: Effort | None = None,
        on_event: OnStreamEvent | None = None,
    ) -> Step:
        content, stop, usage, cost = self.step_wire(
            purpose=purpose, model=model, system=system, messages=messages_to_wire(messages),
            tools=tools_to_wire(tools), max_tokens=max_tokens, effort=effort,
            max_web_searches=sum(t.max_uses for t in tools if isinstance(t, HostedTool) and t.kind == "web_search"),
            on_event=_neutral_events(on_event) if on_event is not None else None,
        )  # fmt: skip
        parts = [part_from_wire(b) for b in content]
        return Step(parts, STOPS.get(stop, "end"), usage_from_counts(usage), cost, stop)

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[Message],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: Effort | None = None,
    ) -> Completion:
        text, parsed, usage, cost, stop = self.complete_wire(
            purpose=purpose, model=model, system=system, messages=messages_to_wire(messages), max_tokens=max_tokens,
            output_model=output_model, effort=effort,
        )  # fmt: skip
        return Completion(text, parsed, model, usage_from_counts(usage), cost, STOPS.get(stop or "", "end"))
