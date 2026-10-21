"""A scripted provider: tests and offline demos, no network, no cost."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from superaide.brain.providers.types import (
    Capabilities,
    Completion,
    ContentPart,
    Effort,
    HostedToolCall,
    HostedToolFinished,
    HostedToolResult,
    HostedToolStarted,
    Message,
    OnStreamEvent,
    PartDone,
    Reasoning,
    ReasoningDelta,
    Step,
    SystemBlock,
    Text,
    TextDelta,
    Tool,
    ToolCall,
    ToolCallReady,
    Usage,
)

# purpose, system, messages, output_model -> a string, a list of parts, a Step, or a Pydantic object
NeutralResponder = Callable[[str, list[SystemBlock], list[Message], type[BaseModel] | None], Any]


def _stream(parts: list[ContentPart], on_event: OnStreamEvent) -> None:
    for part in parts:
        if isinstance(part, Text):
            on_event(TextDelta(part.text))
        elif isinstance(part, Reasoning):
            on_event(ReasoningDelta(part.text))
        elif isinstance(part, ToolCall):
            on_event(ToolCallReady(part))
        elif isinstance(part, HostedToolCall):
            on_event(HostedToolStarted(part))
        elif isinstance(part, HostedToolResult):
            on_event(HostedToolFinished(part))
        on_event(PartDone(part))


@dataclass
class FakeProvider:
    responder: NeutralResponder
    capabilities_: Capabilities = field(default_factory=lambda: Capabilities(structured_output="json_schema"))
    calls: list[dict[str, Any]] = field(default_factory=list)
    id: str = "fake"
    kind: str = "fake"

    def capabilities(self, model: str) -> Capabilities:
        return self.capabilities_

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
        self.calls.append({"purpose": purpose, "model": model, "messages": messages, "tools": tools})
        out = self.responder(purpose, system, messages, None)
        if isinstance(out, Step):
            step = out
        elif isinstance(out, list):
            parts: list[ContentPart] = list(out)
            step = Step(parts, "tool_calls" if any(isinstance(p, ToolCall) for p in parts) else "end", Usage())
        else:
            step = Step([Text(out.reply if hasattr(out, "reply") else str(out))], "end", Usage())
        if on_event is not None:
            _stream(step.parts, on_event)
        return step

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
        self.calls.append({"purpose": purpose, "model": model, "messages": messages})
        out = self.responder(purpose, system, messages, output_model)
        parsed = None
        if output_model is not None:
            parsed = out if isinstance(out, output_model) else output_model.model_validate(out)
            text = parsed.model_dump_json()
        else:
            text = str(out)
        return Completion(text=text, parsed=parsed, model=model, usage=Usage())


def from_legacy(blocks: list[dict[str, Any]]) -> list[ContentPart]:
    """Translate a test script written in the old Anthropic block shape into neutral parts,
    so tests can move to the neutral interface one file at a time. Remove once none use it."""
    from superaide.brain.providers.anthropic import part_from_wire

    return [part_from_wire(dict(b)) for b in blocks]
