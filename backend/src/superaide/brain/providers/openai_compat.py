"""Any OpenAI-compatible Chat Completions service: OpenAI, Azure OpenAI (v1), OpenRouter, xAI,
Gemini's compatibility endpoint, Ollama, vLLM and others (ADR 0007, section 4).

Chat Completions is the dialect they all share. Sources reach the model as labelled text and
come back as `[id]` markers that brain/citations.py checks; provider-run web search isn't used
here (Superaide's own tools stand in). Every call goes through the same spend path as Claude:
the worst case is reserved first, `max_completion_tokens` bounds reasoning and output together,
streams ask for usage, and a response without usage is settled at its reservation.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from superaide.brain import citations
from superaide.brain.providers.base import CheckReport, Guard, Priced, mark_billable, metered
from superaide.brain.providers.presets import Preset
from superaide.brain.providers.types import (
    Capabilities,
    Completion,
    ContentPart,
    Effort,
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
    sources_in,
)
from superaide.brain.spend.pricing import PriceRegistry, UsageCounts, worst_case_cost

log = logging.getLogger(__name__)
KEY = "openai"
STOPS: dict[str, StopReason] = {
    "stop": "end",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


# --- the request ------------------------------------------------------------------


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI's strict JSON Schema: closed objects, every property required (optional ones nullable)."""
    out = dict(schema)
    if out.get("type") == "object" or "properties" in out:
        props = {k: strict_schema(v) for k, v in (out.get("properties") or {}).items()}
        required = set(out.get("required") or [])
        for name, prop in props.items():
            if name not in required:
                props[name] = {"anyOf": [prop, {"type": "null"}]}
                prop.pop("default", None)
        out["properties"] = props
        out["required"] = list(props)
        out["additionalProperties"] = False
    for key in ("items", "additionalItems"):
        if isinstance(out.get(key), dict):
            out[key] = strict_schema(out[key])
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(out.get(key), list):
            out[key] = [strict_schema(s) for s in out[key]]
    if isinstance(out.get("$defs"), dict):
        out["$defs"] = {k: strict_schema(v) for k, v in out["$defs"].items()}
    return out


def system_text(system: list[SystemBlock], extra: str = "") -> str:
    return "\n\n".join([b.text for b in system] + ([extra] if extra else []))


def result_text(result: ToolResult) -> str:
    if isinstance(result.content, str):
        return result.content
    return citations.render_sources(result.content) or "Nothing found."


def to_chat_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m.role == "assistant":
            text = "".join(
                str((p.provider_data or {}).get(KEY, {}).get("text", p.text)) for p in m.parts if isinstance(p, Text)
            )
            calls = [
                {"id": p.id, "type": "function", "function": {"name": p.name, "arguments": json.dumps(p.args)}}
                for p in m.parts
                if isinstance(p, ToolCall)
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        texts: list[str] = []
        for p in m.parts:
            if isinstance(p, ToolResult):
                out.append({"role": "tool", "tool_call_id": p.call_id, "content": result_text(p)})
            elif isinstance(p, Text) and p.text:
                texts.append(p.text)
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


def to_chat_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in tools
        if isinstance(t, ToolSpec)  # provider-run tools aren't offered here; Superaide's own stand in
    ]


# --- the response -----------------------------------------------------------------


def usage_of(raw: Any) -> Priced:
    u = _get(raw, "usage")
    if u is None:
        return Priced(UsageCounts(), _get(raw, "id"), known=False)
    prompt = int(_get(u, "prompt_tokens") or 0)
    cached = int(_get(_get(u, "prompt_tokens_details"), "cached_tokens") or 0)
    cost = _get(u, "cost")
    return Priced(
        UsageCounts(
            input_tokens=max(0, prompt - cached),
            output_tokens=int(_get(u, "completion_tokens") or 0),
            cache_read_tokens=cached,
        ),
        _get(raw, "id"),
        reported_cost_usd=Decimal(str(cost)) if cost is not None else None,
    )


def neutral_usage(p: Priced, raw: Any) -> Usage:
    reasoning = _get(_get(_get(raw, "usage"), "completion_tokens_details"), "reasoning_tokens") or 0
    return Usage(
        input_tokens=p.usage.input_tokens,
        output_tokens=p.usage.output_tokens,
        cache_read_tokens=p.usage.cache_read_tokens,
        reasoning_tokens=int(reasoning),
        reported_cost_usd=p.reported_cost_usd,
        known=p.known,
    )


def listed_prices(models: list[Any]) -> dict[str, dict[str, str]]:
    """Per-model prices from an OpenRouter-style models list (US$ per token, as strings), turned into
    US$ per million tokens. Variable or missing prices (negative, empty) are skipped."""
    out: dict[str, dict[str, str]] = {}
    for m in models:
        pricing = _get(m, "pricing") or (getattr(m, "model_extra", None) or {}).get("pricing")
        try:
            inp = Decimal(str(_get(pricing, "prompt")))
            outp = Decimal(str(_get(pricing, "completion")))
        except ArithmeticError, ValueError, TypeError:
            continue
        if inp < 0 or outp < 0:
            continue
        price = {"input": str(inp * 1_000_000), "output": str(outp * 1_000_000)}
        read = _get(pricing, "input_cache_read")
        if read not in (None, "", "0") and inp > 0:
            try:
                price["cache_read"] = str(Decimal(str(read)) * 1_000_000)
            except ArithmeticError, ValueError:
                pass
        out[str(_get(m, "id"))] = price
    return out


def parse_args(raw: str) -> dict[str, Any]:
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        log.warning("a tool call's arguments weren't valid JSON; passing none")
        return {}
    return args if isinstance(args, dict) else {}


def parts_from_message(message: Any, sources: list[SourceDoc]) -> list[ContentPart]:
    parts: list[ContentPart] = []
    reasoning = _get(message, "reasoning_content") or _get(message, "reasoning")
    if reasoning:
        parts.append(Reasoning(str(reasoning)))
    content = _get(message, "content") or ""
    if content:
        texts, _dropped = citations.extract(str(content), sources)
        if texts:
            texts[0].provider_data = {KEY: {"text": str(content)}}  # the model's own words, markers and all
        parts.extend(texts)
    for call in _get(message, "tool_calls") or []:
        fn = _get(call, "function")
        parts.append(ToolCall(str(_get(call, "id")), str(_get(fn, "name")), parse_args(str(_get(fn, "arguments")))))
    return parts


class _Accumulated:
    """A streamed completion put back together, shaped like a non-streamed one."""

    def __init__(self) -> None:
        self.id: str | None = None
        self.usage: Any = None
        self.content = ""
        self.reasoning = ""
        self.finish: str | None = None
        self.calls: dict[int, dict[str, Any]] = {}

    def message(self) -> dict[str, Any]:
        calls = [
            {"id": c["id"], "function": {"name": c["name"], "arguments": c["arguments"]}}
            for _, c in sorted(self.calls.items())
        ]
        return {"content": self.content, "reasoning_content": self.reasoning, "tool_calls": calls}


# --- the provider ---------------------------------------------------------------------


class OpenAICompatProvider:
    kind = "openai_compat"

    def __init__(
        self,
        guard: Guard,
        preset: Preset,
        *,
        api_key: str = "",
        base_url: str = "",
        id: str | None = None,
        client: Any = None,
    ) -> None:
        self.guard, self.preset = guard, preset
        self.id = id or preset.id
        self.base_url = base_url or preset.base_url
        if client is None:
            import openai

            if preset.key_required and not api_key:
                raise RuntimeError(f"{preset.label} needs an API key")
            if not self.base_url:
                raise RuntimeError(f"{preset.label} needs a base URL")
            headers = {"api-key": api_key} if preset.azure_key_header and api_key else None
            from superaide.platform.security.urls import safe_client

            client = openai.OpenAI(
                api_key=api_key or "not-needed", base_url=self.base_url, max_retries=2, timeout=120,
                default_headers=headers,
                # Each connection is checked when made, so a name can't later point inside our network.
                http_client=safe_client(allow_private=preset.local, for_openai=True, timeout=120),
            )  # fmt: skip
        self.client = client

    @property
    def prices(self) -> PriceRegistry | None:
        return getattr(self.guard, "prices", None)

    @property
    def price_key(self) -> str:
        """Prices and the ledger use the endpoint's key (openai, openrouter, custom-2…)."""
        return self.id

    def capabilities(self, model: str) -> Capabilities:
        return self.preset.capabilities

    def check(self) -> CheckReport:
        try:
            page = self.client.models.list()
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in (401, 403):
                return CheckReport(False, f"{self.preset.label} didn't accept that key.")
            if status == 404:
                return CheckReport(
                    False, "That address answered, but not like an OpenAI-compatible API. Check the base URL."
                )
            return CheckReport(False, f"Couldn't reach {self.preset.label} ({type(exc).__name__}). Check the address.")
        data = _get(page, "data") or []
        models = sorted(str(_get(m, "id")) for m in data)
        prices = listed_prices(data) if self.preset.reports_cost else {}
        return CheckReport(True, f"Connected to {self.preset.label}", models, prices)

    # --- calls ---

    def _worst(self, model: str, request: dict[str, Any], max_tokens: int) -> Decimal:
        chars = len(json.dumps(request.get("messages", []), default=str)) + len(json.dumps(request.get("tools", [])))
        chars += len(json.dumps(request.get("response_format", {})))
        return worst_case_cost(model, chars, max_tokens, cache_write=False, provider=self.price_key, prices=self.prices)

    def _request(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int,
        effort: Effort | None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {"model": model, "messages": to_chat_messages(system, messages)}
        request[self.preset.max_tokens_param] = max_tokens
        chat_tools = to_chat_tools(tools)
        if chat_tools:
            request["tools"] = chat_tools
            if self.preset.capabilities.parallel_tools is False:
                request["parallel_tool_calls"] = False
        if effort and self.preset.capabilities.reasoning:
            request["reasoning_effort"] = effort
        return request

    def _call(
        self, purpose: str, model: str, request: dict[str, Any], max_tokens: int, on_event: OnStreamEvent | None
    ) -> tuple[Any, Any, Decimal]:
        """Returns (finish_reason holder, message, cost)."""
        if on_event is None:
            raw, _usage, cost = metered(
                self.guard, purpose=purpose, model=model, worst_case_usd=self._worst(model, request, max_tokens),
                call=lambda: self.client.chat.completions.create(**request), priced=usage_of, provider=self.price_key,
            )  # fmt: skip
            choice = (_get(raw, "choices") or [None])[0]
            return raw, _get(choice, "message"), cost
        acc, _usage, cost = metered(
            self.guard, purpose=purpose, model=model, worst_case_usd=self._worst(model, request, max_tokens),
            call=lambda: self._stream(request, on_event), priced=usage_of, provider=self.price_key,
        )  # fmt: skip
        return acc, acc.message(), cost

    def _stream(self, request: dict[str, Any], on_event: OnStreamEvent) -> _Accumulated:
        acc = _Accumulated()
        markers = citations.MarkerFilter()  # the owner sees the text without [ava://…] markers as it streams
        stream = self.client.chat.completions.create(**request, stream=True, stream_options={"include_usage": True})
        started = False
        try:
            for chunk in stream:
                started = True
                acc.id = acc.id or _get(chunk, "id")
                if _get(chunk, "usage") is not None:
                    acc.usage = _get(chunk, "usage")
                for choice in _get(chunk, "choices") or []:
                    delta = _get(choice, "delta")
                    if _get(choice, "finish_reason"):
                        acc.finish = _get(choice, "finish_reason")
                    text = _get(delta, "content")
                    if text:
                        acc.content += text
                        if shown := markers.feed(text):
                            on_event(TextDelta(shown))
                    thought = _get(delta, "reasoning_content") or _get(delta, "reasoning")
                    if thought:
                        acc.reasoning += thought
                        on_event(ReasoningDelta(thought))
                    for call in _get(delta, "tool_calls") or []:
                        index = int(_get(call, "index") or 0)
                        slot = acc.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        fn = _get(call, "function")
                        slot["id"] = _get(call, "id") or slot["id"]
                        slot["name"] = _get(fn, "name") or slot["name"]
                        slot["arguments"] += _get(fn, "arguments") or ""
        except Exception as exc:
            if started:
                mark_billable(exc)  # the answer had begun, so the provider may have billed for it
            raise
        if rest := markers.flush():
            on_event(TextDelta(rest))
        return acc

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
        rule = citations.RULE if any(isinstance(t, ToolSpec) for t in tools) else ""
        request = self._request(
            model=model, system=system_text(system, rule), messages=messages, tools=tools, max_tokens=max_tokens,
            effort=effort,
        )  # fmt: skip
        raw, message, cost = self._call(purpose, model, request, max_tokens, on_event)
        sources = sources_in([p for m in messages for p in m.parts])
        parts = parts_from_message(message, sources)
        if on_event is not None:
            for part in parts:
                if isinstance(part, ToolCall):
                    on_event(ToolCallReady(part))
                on_event(PartDone(part))
        finish = (
            raw.finish if isinstance(raw, _Accumulated) else _get((_get(raw, "choices") or [None])[0], "finish_reason")
        )
        stop: StopReason = STOPS.get(str(finish), "end")
        if any(isinstance(p, ToolCall) for p in parts):
            stop = "tool_calls"  # some servers say "stop" even when they called a tool
        return Step(parts, stop, neutral_usage(usage_of(raw), raw), cost, str(finish or ""))

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
        mode = self.preset.capabilities.structured_output
        extra = ""
        if output_model is not None and mode != "json_schema":
            schema = json.dumps(output_model.model_json_schema())
            extra = f"Answer with one JSON object only, no prose, matching this JSON Schema:\n{schema}"
        request = self._request(
            model=model, system=system_text(system, extra), messages=messages, tools=[], max_tokens=max_tokens,
            effort=effort,
        )  # fmt: skip
        if output_model is not None:
            if mode == "json_schema":
                request["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": output_model.__name__,
                        "schema": strict_schema(output_model.model_json_schema()),
                        "strict": True,
                    },
                }
            elif mode == "json_mode":
                request["response_format"] = {"type": "json_object"}
        total = Decimal(0)
        attempts = 2 if output_model is not None else 1  # one retry with the error, then give up
        text, parsed, finish, raw = "", None, None, None
        for attempt in range(attempts):
            raw, message, cost = self._call(purpose, model, request, max_tokens, None)
            total += cost
            text = str(_get(message, "content") or "")
            finish = _get((_get(raw, "choices") or [None])[0], "finish_reason")
            if output_model is None or finish == "length":
                break
            try:
                parsed = output_model.model_validate_json(_json_only(text))
                break
            except ValidationError as exc:
                log.warning(
                    "%s: output did not match %s (attempt %d): %s", purpose, output_model.__name__, attempt + 1, exc
                )
                request = dict(request)
                request["messages"] = [
                    *request["messages"],
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"That wasn't valid: {exc.errors()[0]['msg']}. Reply with the JSON only.",
                    },
                ]
        usage = neutral_usage(usage_of(raw), raw)
        return Completion(text, parsed, model, usage, total, STOPS.get(str(finish), "end"))


def _json_only(text: str) -> str:
    """Models without JSON mode sometimes wrap the object in prose or a code fence."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text
