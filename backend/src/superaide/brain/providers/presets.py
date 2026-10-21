"""OpenAI-compatible services as data, not subclasses (ADR 0007, section 4).

"OpenAI-compatible" isn't a standard: each service differs a little. A preset records what we
rely on: where it lives, how it authenticates, which token-limit parameter it takes, whether it
reports its own charge, and what it can do.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from superaide.brain.providers.types import Capabilities


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    base_url: str  # default; "" means the owner must enter it
    key_required: bool = True
    key_hint: str = ""  # what a key looks like, for the form placeholder
    guide_url: str = ""
    local: bool = False  # a server the owner runs; free when its address is local
    azure_key_header: bool = False  # also send the key as `api-key` (Azure)
    max_tokens_param: str = "max_completion_tokens"  # bounds reasoning + output where supported
    reports_cost: bool = False  # usage.cost in each response (OpenRouter)
    capabilities: Capabilities = field(default_factory=Capabilities)
    note: str = ""


_HOSTED = Capabilities(
    tools=True,
    parallel_tools=True,
    streaming=True,
    reasoning=True,
    native_citations=False,
    server_web_search=False,
    server_web_fetch=False,
    structured_output="json_schema",
    prompt_caching="automatic",
    vision=True,
)
_LOCAL = Capabilities(
    tools=True,
    parallel_tools=False,
    streaming=True,
    reasoning=False,
    structured_output="json_mode",
    prompt_caching="none",
    vision=False,
    # Local servers may ignore max_completion_tokens for reasoning; they're free, so the cap is safe.
    max_output_tokens_bounds_reasoning=False,
)

PRESETS: dict[str, Preset] = {
    p.id: p
    for p in [
        Preset(
            "openai", "OpenAI", "https://api.openai.com/v1", key_hint="sk-…",
            guide_url="https://platform.openai.com/api-keys", capabilities=_HOSTED,
        ),
        Preset(
            "azure", "Azure OpenAI / AI Foundry", "", key_hint="your resource key",
            guide_url="https://ai.azure.com", azure_key_header=True, capabilities=_HOSTED,
            note="Base URL is https://<resource>.openai.azure.com/openai/v1/ and the model is your deployment name.",
        ),
        Preset(
            "openrouter", "OpenRouter", "https://openrouter.ai/api/v1", key_hint="sk-or-…",
            guide_url="https://openrouter.ai/keys", reports_cost=True, capabilities=_HOSTED,
        ),
        Preset(
            "xai", "xAI Grok", "https://api.x.ai/v1", key_hint="xai-…", guide_url="https://console.x.ai",
            capabilities=_HOSTED,
        ),
        Preset(
            "gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/",
            key_hint="AIza…", guide_url="https://aistudio.google.com/apikey", capabilities=_HOSTED,
        ),
        Preset(
            "ollama", "Ollama (on this computer)", "http://localhost:11434/v1", key_required=False,
            guide_url="https://ollama.com/download", local=True, max_tokens_param="max_tokens", capabilities=_LOCAL,
            note="Free and private, but slower, and smaller models are less reliable with tools.",
        ),
        Preset(
            "vllm", "vLLM / LM Studio (local server)", "http://localhost:8000/v1", key_required=False, local=True,
            max_tokens_param="max_tokens", capabilities=_LOCAL,
        ),
        Preset(
            "custom", "Other OpenAI-compatible service", "", key_required=False, local=True,
            max_tokens_param="max_tokens", capabilities=_LOCAL,
            note="Free only when its address is on this computer or your network; otherwise set a price.",
        ),
    ]
}  # fmt: skip
