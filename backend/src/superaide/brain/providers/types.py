"""The provider-neutral model every adapter speaks (docs/decisions/0007-pluggable-providers.md).

Field names follow the Vercel AI SDK (LanguageModelV3) and Pydantic AI where they overlap.
`provider_data` holds the adapter's own raw block: the adapter that produced a part re-emits it
unchanged on the next step (Anthropic server tools and signed thinking require that); any other
adapter ignores it and uses only the neutral fields.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

Role = Literal["user", "assistant"]
Effort = Literal["low", "medium", "high"]
StopReason = Literal["end", "tool_calls", "pause", "max_tokens", "refusal", "error"]
SourceKind = Literal["memory", "message", "calendar", "web", "other"]


@dataclass
class SystemBlock:
    text: str
    cache: bool = False  # mark the end of the stable prefix for prompt caching


@dataclass
class SourceDoc:
    """Something Ava looked up and may cite: `id` is an `ava://` uri or a URL."""

    id: str
    title: str
    text: str
    kind: SourceKind = "other"


def source_kind(uri: str) -> SourceKind:
    if uri.startswith("ava://memory/"):
        return "memory"
    if uri.startswith("ava://msg/"):
        return "message"
    if uri.startswith("ava://cal/"):
        return "calendar"
    if uri.startswith(("http://", "https://")):
        return "web"
    return "other"


@dataclass
class Citation:
    source_id: str
    title: str = ""
    cited_text: str = ""
    kind: SourceKind = "other"
    verified: bool = True  # native citations are; parsed markers are once checked against this turn's sources


@dataclass
class Text:
    text: str
    citations: list[Citation] = field(default_factory=list)
    provider_data: dict[str, Any] | None = None


@dataclass
class Reasoning:
    text: str
    provider_data: dict[str, Any] | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
    provider_data: dict[str, Any] | None = None


@dataclass
class ToolResult:
    call_id: str
    content: str | list[SourceDoc]
    is_error: bool = False
    provider_data: dict[str, Any] | None = None


@dataclass
class HostedToolCall:
    """A tool the provider runs itself (e.g. Anthropic's web_search server tool)."""

    id: str
    kind: str
    args: dict[str, Any]
    provider_data: dict[str, Any] | None = None


@dataclass
class HostedToolResult:
    call_id: str
    kind: str
    sources: list[SourceDoc] = field(default_factory=list)
    error: str | None = None
    provider_data: dict[str, Any] | None = None


ContentPart = Text | Reasoning | ToolCall | ToolResult | HostedToolCall | HostedToolResult


@dataclass
class Message:
    role: Role
    parts: list[ContentPart]


@dataclass
class ToolSpec:
    """MCP's tool shape: name, description and a JSON Schema for the input."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class HostedTool:
    """A request for a provider-run tool; an adapter without it gets Superaide's own tool instead."""

    kind: Literal["web_search", "web_fetch"]
    max_uses: int = 3


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0  # informational; already counted in output_tokens
    hosted_tool_calls: dict[str, int] = field(default_factory=dict)
    reported_cost_usd: Decimal | None = None  # what the provider says it charged, when it says
    extras: dict[str, Any] = field(default_factory=dict)  # provider detail, e.g. Anthropic's cache-write split
    known: bool = True  # False when the provider sent no usage: settle at the reservation


@dataclass
class Step:
    """One model call inside a tool loop."""

    parts: list[ContentPart]
    stop: StopReason
    usage: Usage
    cost_usd: Decimal = Decimal(0)
    raw_stop: str = ""


@dataclass
class Completion:
    text: str
    parsed: Any
    model: str
    usage: Usage
    cost_usd: Decimal = Decimal(0)
    stop: StopReason = "end"


@dataclass(frozen=True)
class Capabilities:
    tools: bool = True
    parallel_tools: bool = True
    streaming: bool = True
    reasoning: bool = False
    native_citations: bool = False
    server_web_search: bool = False
    server_web_fetch: bool = False
    structured_output: Literal["json_schema", "json_mode", "none"] = "none"
    prompt_caching: Literal["explicit", "automatic", "none"] = "none"
    vision: bool = False
    max_output_tokens_bounds_reasoning: bool = True


# --- stream events ----------------------------------------------------------------


@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class ToolCallReady:
    call: ToolCall


@dataclass
class HostedToolStarted:
    call: HostedToolCall


@dataclass
class HostedToolFinished:
    result: HostedToolResult


@dataclass
class PartDone:
    part: ContentPart


StreamEvent = TextDelta | ReasoningDelta | ToolCallReady | HostedToolStarted | HostedToolFinished | PartDone
OnStreamEvent = Callable[[StreamEvent], None]


def sources_in(parts: list[ContentPart]) -> list[SourceDoc]:
    """Every source that entered the conversation through these parts."""
    out: list[SourceDoc] = []
    for p in parts:
        if isinstance(p, ToolResult) and isinstance(p.content, list):
            out.extend(p.content)
        elif isinstance(p, HostedToolResult):
            out.extend(p.sources)
    return out


def tainted(parts: list[ContentPart]) -> bool:
    """Untrusted (web) content entered the turn, however it arrived."""
    return any(isinstance(p, HostedToolResult) for p in parts) or any(s.kind == "web" for s in sources_in(parts))
