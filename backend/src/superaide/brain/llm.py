"""The model interface the tool loop uses today (Messages API dicts). Every call goes through the budget guard.

ClaudeLLM is a thin shim over the Anthropic provider (brain/providers/anthropic.py). FakeLLM returns
scripted outputs so tests and offline demos cost nothing. See docs/decisions/0007-pluggable-providers.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from superaide.brain.providers.types import SystemBlock
from superaide.brain.spend.budget import BudgetGuard
from superaide.brain.spend.pricing import UsageCounts

T = TypeVar("T", bound=BaseModel)

# Live progress from a streaming step: ("text", {"delta"}), ("thinking", {"delta"}), and
# ("block", {"block"}) as each content block completes (server tool calls and results).
OnEvent = Callable[[str, dict[str, Any]], None]


@dataclass
class LLMResult:
    text: str
    parsed: Any
    model: str
    usage: UsageCounts
    cost_usd: Decimal = Decimal(0)
    stop_reason: str | None = None


@dataclass
class StepResult:
    """One Messages API call inside a tool loop: content blocks as plain dicts (sent back
    unchanged on the next step, as server tools require), why it stopped, and its cost."""

    content: list[dict[str, Any]]
    stop_reason: str
    usage: UsageCounts
    cost_usd: Decimal = Decimal(0)


class LLM(Protocol):
    def step(
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
        on_event: OnEvent | None = None,
    ) -> StepResult: ...

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: str | None = None,
    ) -> LLMResult: ...


class ClaudeLLM:
    """The tool loop's dict interface on top of the Anthropic provider (brain/providers/anthropic.py).
    Kept until the loop speaks neutral parts; requests are identical either way."""

    def __init__(self, guard: BudgetGuard, api_key: str = "", client: Any = None) -> None:
        from superaide.brain.providers.anthropic import AnthropicProvider

        self.provider = AnthropicProvider(guard, api_key=api_key, client=client)
        self.client = self.provider.client
        self.guard = guard

    def step(
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
        on_event: OnEvent | None = None,
    ) -> StepResult:
        content, stop_reason, usage, cost = self.provider.step_wire(
            purpose=purpose, model=model, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
            effort=effort, max_web_searches=max_web_searches, on_event=on_event,
        )  # fmt: skip
        return StepResult(content=content, stop_reason=stop_reason, usage=usage, cost_usd=cost)

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: str | None = None,
    ) -> LLMResult:
        text, parsed, usage, cost, stop_reason = self.provider.complete_wire(
            purpose=purpose, model=model, system=system, messages=messages, max_tokens=max_tokens,
            output_model=output_model, effort=effort,
        )  # fmt: skip
        return LLMResult(text=text, parsed=parsed, model=model, usage=usage, cost_usd=cost, stop_reason=stop_reason)


STOP_NAMES = {"end": "end_turn", "tool_calls": "tool_use", "pause": "pause_turn", "max_tokens": "max_tokens"}


class ProviderLLM:
    """The tool loop's dict interface on top of any neutral provider (e.g. OpenAI-compatible).
    Conversation dicts are read into neutral messages; the provider's parts come back as the
    same block shapes the loop already understands, citations included."""

    def __init__(self, provider: Any, web: Any = None) -> None:
        self.provider = provider
        self.guard = getattr(provider, "guard", None)
        self.web = web  # Superaide's own web tools (tools/web.py), standing in for provider-run ones
        self.offered: set[str] = set()  # the stand-ins offered in the last step; only those may run

    def run_substitute(self, name: str, args: dict[str, Any]) -> tuple[Any, bool] | None:
        if self.web is None or name not in self.offered:
            return None
        return self.web.run(name, args)

    def step(
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
        on_event: OnEvent | None = None,
    ) -> StepResult:
        from superaide.brain.providers import types as t
        from superaide.brain.providers.anthropic import messages_from_wire, part_to_wire, tools_from_wire

        caps = self.provider.capabilities(model)
        native = {"web_search": caps.server_web_search, "web_fetch": caps.server_web_fetch}
        own = self.web.specs() if self.web is not None else {}
        neutral_tools: list[t.Tool] = []
        self.offered = set()
        for x in tools_from_wire(tools):
            if not isinstance(x, t.HostedTool) or native[x.kind]:
                neutral_tools.append(x)
            elif x.kind in own:  # the provider can't search or read the web itself: use ours
                spec = own[x.kind]
                neutral_tools.append(t.ToolSpec(spec["name"], spec["description"], spec["input_schema"]))
                self.offered.add(spec["name"])

        def relay(event: t.StreamEvent) -> None:
            assert on_event is not None
            if isinstance(event, t.TextDelta):
                on_event("text", {"delta": event.text})
            elif isinstance(event, t.ReasoningDelta):
                on_event("thinking", {"delta": event.text})
            elif isinstance(event, t.PartDone):
                on_event("block", {"block": part_to_wire(event.part)})

        step = self.provider.step(
            purpose=purpose, model=model, system=system, messages=messages_from_wire(messages), tools=neutral_tools,
            max_tokens=max_tokens, effort=effort, on_event=relay if on_event is not None else None,
        )  # fmt: skip
        content = [
            part_to_wire(p) for p in step.parts if not (isinstance(p, t.Text) and not p.text and not p.citations)
        ]
        usage = UsageCounts(
            input_tokens=step.usage.input_tokens,
            output_tokens=step.usage.output_tokens,
            cache_read_tokens=step.usage.cache_read_tokens,
        )
        return StepResult(content, STOP_NAMES.get(step.stop, step.stop), usage, step.cost_usd)

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: str | None = None,
    ) -> LLMResult:
        from superaide.brain.providers.anthropic import messages_from_wire

        done = self.provider.complete(
            purpose=purpose, model=model, system=system, messages=messages_from_wire(messages), max_tokens=max_tokens,
            output_model=output_model, effort=effort,
        )  # fmt: skip
        usage = UsageCounts(input_tokens=done.usage.input_tokens, output_tokens=done.usage.output_tokens)
        return LLMResult(done.text, done.parsed, model, usage, done.cost_usd, STOP_NAMES.get(done.stop, done.stop))


class RoutedLLM:
    """Several providers behind one interface: a model id "<preset>::<model>" runs on that
    provider, a bare id on Claude (see brain/providers/config.py)."""

    def __init__(self, routes: dict[str, Any], fallback: str | None = None) -> None:
        self.routes = routes
        self.fallback = fallback  # the chat model, for a role whose provider isn't connected
        first = next(iter(routes.values()), None)
        self.guard = getattr(first, "guard", None)

    def _route(self, model: str) -> tuple[Any, str]:
        from superaide.brain.providers.config import split

        provider, name = split(model)
        llm = self.routes.get(provider)
        if llm is None and self.fallback and self.fallback != model:
            return self._route(self.fallback)
        if llm is None:
            raise RuntimeError(f"The model provider {provider!r} isn't connected")
        return llm, name

    def run_substitute(self, name: str, args: dict[str, Any]) -> tuple[Any, bool] | None:
        for llm in self.routes.values():
            run = getattr(llm, "run_substitute", None)
            if run is not None and (out := run(name, args)) is not None:
                return out
        return None

    def step(self, *, model: str, **kwargs: Any) -> StepResult:
        llm, name = self._route(model)
        kwargs, mapping = _through_airlock(kwargs)
        finish = None
        if mapping and kwargs.get("on_event") is not None:
            from superaide.privacy.airlock import restoring_events

            kwargs["on_event"], finish = restoring_events(kwargs["on_event"], mapping)
        result = llm.step(model=name, **kwargs)
        if finish is not None:
            finish()
        if mapping:
            from superaide.privacy.airlock import restore_any

            result.content = restore_any(result.content, mapping)
        return result

    def complete(self, *, model: str, **kwargs: Any) -> LLMResult:
        llm, name = self._route(model)
        kwargs, mapping = _through_airlock(kwargs)
        result = llm.complete(model=name, **kwargs)
        if mapping:
            from superaide.privacy.airlock import restore_any

            result.text = restore_any(result.text, mapping)
            result.parsed = restore_any(result.parsed, mapping)
        return result


def placeholder_note(mapping: dict[str, str]) -> str:
    """What `[email 1]` means (issue #338). Without it the model reads a bare bracketed token as an unfilled
    template and asks for "the real address". Names the kinds present, never a value. Only added when
    something was replaced, so a conversation with nothing redacted is unchanged."""
    kinds = sorted({k.strip("[]").rsplit(" ", 1)[0] for k in mapping})
    return (
        "Privacy placeholders: some details in this conversation are shown as tokens like "
        + ", ".join(f"[{k} 1]" for k in kinds[:6])
        + ". Each is a real value hidden from you for privacy, not a template field. Use the token exactly as "
        "written, in your answer and in tool arguments (for example as a recipient); Superaide replaces it with "
        "the real value before anything is shown or sent. Never ask the owner to retype it, say it looks like a "
        "placeholder, or refuse because of it."
    )


def _through_airlock(kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """With AIRLOCK_ENABLED on (the default), other people's contact details, ids, addresses and names in the
    conversation are replaced by placeholders before anything is uploaded (privacy/airlock.py); the map stays
    here and is used to put the originals back into the reply, including text streamed to the owner. Off
    (AIRLOCK_ENABLED=false): nothing changes."""
    from superaide.platform.config import get_settings

    if not get_settings().airlock_enabled or "messages" not in kwargs:
        return kwargs, {}
    from superaide.privacy.airlock import redact_messages
    from superaide.privacy.people import known_people

    names, keep = known_people()
    messages, r = redact_messages(kwargs["messages"], names=names, keep=keep)
    out = {**kwargs, "messages": messages}
    if r.mapping and kwargs.get("system") is not None:
        out["system"] = [*kwargs["system"], SystemBlock(placeholder_note(r.mapping))]
    return out, r.mapping


Responder = Callable[[str, list[SystemBlock], list[dict[str, Any]], type[BaseModel] | None], Any]


@dataclass
class FakeLLM:
    """Scripted model. `responder` returns a string, a dict or a Pydantic object."""

    responder: Responder
    calls: list[dict[str, Any]] = field(default_factory=list)

    def step(
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
        on_event: OnEvent | None = None,
    ) -> StepResult:
        """The responder gets the purpose, system and messages; it may return text, a
        list of content blocks, a full {"content", "stop_reason"} dict, or a TurnOutput
        (its reply becomes the text, so the offline echo works in the tool loop too)."""
        self.calls.append({"purpose": purpose, "model": model, "system": system, "messages": messages, "tools": tools})
        out = self.responder(purpose, system, messages, None)
        if isinstance(out, dict) and "content" in out:
            content, stop = list(out["content"]), str(out.get("stop_reason", "end_turn"))
        elif isinstance(out, list):
            content = list(out)
            stop = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
        else:
            text = out.reply if hasattr(out, "reply") else str(out)
            content, stop = [{"type": "text", "text": text}], "end_turn"
        if on_event is not None:
            for block in content:
                if block.get("type") == "text":
                    on_event("text", {"delta": block["text"]})
                elif block.get("type") == "thinking":
                    on_event("thinking", {"delta": block.get("thinking", "")})
                on_event("block", {"block": block})
        return StepResult(content=content, stop_reason=stop, usage=UsageCounts())

    def complete(
        self,
        *,
        purpose: str,
        model: str,
        system: list[SystemBlock],
        messages: list[dict[str, Any]],
        max_tokens: int,
        output_model: type[BaseModel] | None = None,
        effort: str | None = None,
    ) -> LLMResult:
        self.calls.append({"purpose": purpose, "model": model, "system": system, "messages": messages})
        out = self.responder(purpose, system, messages, output_model)
        parsed = None
        if output_model is not None:
            parsed = out if isinstance(out, output_model) else output_model.model_validate(out)
            text = parsed.model_dump_json()
        else:
            text = str(out)
        return LLMResult(text=text, parsed=parsed, model=model, usage=UsageCounts())
