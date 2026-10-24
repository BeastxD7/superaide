# 0007: Pluggable model providers, and one plugin pattern for everything

Status: proposed (2026-10-22). Amends 0006 (the `server_tool` field) and 0004 (where provider keys live).

## Context

The owner wants a settings page to choose Ava's model. Today only Claude works, and it works well: native
`search_result` citations, Anthropic's server tools (`web_search`, `web_fetch`) with `pause_turn`, adaptive
thinking shown live, prompt caching, and a hard spend cap that reserves the worst case before every call.
Later Ava should also run on Azure OpenAI / AI Foundry, OpenRouter, xAI Grok, Ollama or another local server,
OpenAI, Gemini, Bedrock and Vertex. More broadly, providers, skills, channels and connectors should each plug
in the same way.

### Where the code depends on Anthropic today

| Place | Anthropic-specific part |
| --- | --- |
| `brain/llm.py` `ClaudeLLM` | `anthropic.Anthropic` client; `messages.create` / `messages.stream`; `system` as text blocks with `cache_control: ephemeral`; `output_config.effort`; `output_config.format` with `anthropic.transform_schema`; `thinking: {type: adaptive, display: summarized}` only when streaming; stream events `content_block_delta` (`text_delta`, `thinking_delta`) and `content_block_stop`; `_request_id`; content blocks returned as raw dicts. |
| `brain/llm.py` `LLM`, `StepResult`, `FakeLLM` | The protocol's types *are* Anthropic shapes: `messages` and `content` are Messages API dicts, `tools` are Anthropic tool dicts, `stop_reason` uses Anthropic values. `FakeLLM` infers `tool_use` from block types. |
| `brain/loop.py` | Tool results built as `{"type": "tool_result", "tool_use_id", "is_error"}`; the assistant turn is sent back verbatim (`last.content`), which server tools and thinking signatures require; `stop_reason in {"pause_turn", "tool_use"}`; `search_results()` builds `search_result` blocks; `_cited_from` reads `search_result_location`, `web_search_result_location`, `char_location`; `render()` reads `block["citations"]`; `on_model_event` branches on `server_tool_use`, `web_search_tool_result`, `web_fetch_tool_result`, `error_code`; `tainted` is set from those block types; `ACTION_TOOL` uses `input_schema`; `max_web_searches` per step. |
| `brain/pricing.py` | Claude-only `PRICES`; `UsageCounts.from_api` reads Anthropic usage (`cache_creation.ephemeral_5m/1h_input_tokens`, `server_tool_use.web_search_requests`); 5-minute/1-hour cache-write multipliers; `WEB_SEARCH_USD`; Haiku long-context tier. |
| `brain/budget.py`, `db/models.py` `SpendEntry` | Ledger rows have `model` but no `provider`; cache columns assume Anthropic's split. |
| `brain/turn.py`, `brain/charter.md` | `SystemBlock.cache` maps to `cache_control`; the charter is tuned for Claude. |
| `skills/registry.py` `tools_for` | Emits Anthropic tool dicts (`input_schema`), server-tool specs `{type: web_search_20250305, max_uses}` and `web_fetch` with `citations.enabled`. |
| `skills/manifest.py`, `skills/builtin/web-research/superaide.json` | `Tool.server_tool` holds an Anthropic server-tool type string. |
| `skills/calendar.py` | Returns raw `search_result` blocks with `ava://cal/…` sources. |
| `context/recall.py` | Fine as is (`Source` with `ava://` uri); `loop.search_results` is what makes it Anthropic-shaped. |
| `factory.py` | `uses_real_model` = "an Anthropic key is set"; `build_llm` always builds `ClaudeLLM`; the offline `demo_responder` says "Connect Claude". |
| `config.py` | `anthropic_api_key`; `model_chat/triage/deep` are Claude ids. |
| `uses_real_model` callers | `pipeline.py` (web tools on/off), `app/routes.py` (`brain: claude/offline`), `knowledge/facts.py`, `knowledge/ingest.py`, `knowledge/learn.py`, `api/admin.py` (`model_mode`). |
| `cli.py` `hello` | Constructs `ClaudeLLM` directly. |
| `vault/store.py` `FIELDS`, `status()` | Hard-coded `("anthropic", "api_key") -> anthropic_api_key`; one fixed entry per service. |
| `vault/check.py` `check_anthropic` | `sk-ant-` prefix check and `GET /v1/models` with `x-api-key`. |
| `app/connections.py` | `POST /app/connections/anthropic`, `GUIDES["anthropic"]`, disconnect allow-list. |
| Frontend | `features/workspace/Connections.tsx` (Claude card, `sk-ant-` placeholder), `lib/api.ts`, `features/dashboard/Home.tsx`, `AppSidebar.tsx`, `ChatPage.tsx`, `features/console/pages/Overview.tsx`. |
| Tests | `FakeLLM` scripts across `tests/` return Anthropic-shaped dicts (`tool_use`, `search_result_location`). |

Channels and connectors have the same kind of coupling, but to a fixed list rather than to a vendor:
`channels/runner.py` knows Telegram and Slack by name, `factory.build_messengers` and `vault/store.FIELDS`
list them, and each `check_*` lives in `vault/check.py`.

### What current practice does (October 2026)

- **OpenAI-compatible Chat Completions** is the de facto lowest common denominator. Azure OpenAI's v1 API
  takes the plain `OpenAI()` client with `base_url=https://<resource>.openai.azure.com/openai/v1/` and no
  `api-version` ([Microsoft](https://learn.microsoft.com/azure/foundry/openai/api-version-lifecycle)). Ollama
  serves `/v1/chat/completions` with tools, streaming, `stream_options.include_usage`, JSON mode and
  `reasoning_effort`, and `/v1/responses` from v0.13.3 but stateless only
  ([Ollama](https://docs.ollama.com/api/openai-compatibility)). OpenRouter, xAI, vLLM and Gemini's
  compatibility endpoint speak the same dialect. "Compatible" isn't a standard, though: every vendor has quirks.
- **Vercel AI SDK** (LanguageModelV3) normalises output into a content union of text, reasoning, file,
  source, tool-call, tool-result and tool-approval-request, with typed stream parts and an opaque
  `providerMetadata` on each part
  ([spec](https://github.com/vercel/ai/tree/main/packages/provider/src/language-model/v3)).
- **Pydantic AI** separates a *Model* (wraps a vendor API, builds requests) from a *Provider* (auth and base
  URL), and uses *profiles* for per-model-family quirks such as JSON Schema transforms. OpenRouter, Azure and
  Ollama run through `OpenAIChatModel` with different providers. Responses are typed parts carrying
  `provider_details` ([docs](https://pydantic.dev/docs/ai/models/overview/)).
- **OpenAI Agents SDK** supports Responses and Chat Completions natively and reaches everything else through
  a LiteLLM adapter. Hosted tools and tracing are OpenAI-first.
- **LangChain chat models** normalise to `AIMessage` with `tool_calls` and `usage_metadata`. Cost isn't part
  of the shape: a LangChain PR had to add OpenRouter's `cost` fields because they were being dropped.
- **OpenRouter** returns `usage.cost` (credits charged) plus token and reasoning counts in every response
  ([usage accounting](https://openrouter.ai/docs/guides/administration/usage-accounting)).
- **LiteLLM** maps 100+ providers onto the OpenAI shape and estimates cost from
  `model_prices_and_context_window.json`, which it fetches from GitHub at runtime unless
  `LITELLM_LOCAL_MODEL_COST_MAP=True` ([docs](https://docs.litellm.ai/docs/completion/token_usage)).
  **On 24 March 2026, PyPI releases 1.82.7 and 1.82.8 shipped credential-stealing code** that wasn't in the
  GitHub source. 1.82.8 used a `.pth` file, so it ran at interpreter start-up without `import litellm`, and
  it exfiltrated environment variables, SSH keys and cloud credentials. It was live for about five hours and
  has been attributed to the TeamPCP group, which had earlier breached Trivy
  ([BleepingComputer](https://bleepingcomputer.com/news/security/popular-litellm-pypi-package-compromised-in-teampcp-supply-chain-attack),
  [NetSPI](https://www.netspi.com/blog/executive-blog/ai-ml-pentesting/litellm-supply-chain-compromise/),
  [Orca](https://orca.security/resources/blog/litellm-supply-chain-attack-malware/)). These are secondary
  reports and differ on the entry point.

## Decision

### 1. Our own thin adapters on the vendors' official SDKs. No LiteLLM.

We write an `LLMProvider` interface and two real adapters: **Anthropic** on `anthropic` and
**OpenAI-compatible** on `openai`. Both are first-party SDKs we already trust or would have to trust anyway.
Reasons:

1. **The hard cap needs prices we control before the call.** `BudgetGuard.reserve` refuses unpriced models.
   LiteLLM's default cost map is downloaded at runtime from a repository we don't control, and its cost is
   worked out after the call. We'd keep our own registry anyway, so LiteLLM adds nothing on the part that
   must never be wrong.
2. **Supply-chain exposure matches our threat model exactly.** This process holds the vault master key and
   decrypted owner credentials in memory. The March 2026 payload stole exactly that kind of material, and
   it ran without an import. LiteLLM is a large, fast-moving dependency tree. Two SDKs from the model vendors
   are a much smaller surface.
3. **Normalising to the OpenAI shape is lossy for our best path.** `search_result` citations, server tools
   with `pause_turn`, and signed thinking blocks sent back verbatim are what make Ava's Claude answers
   cited and honest. A lowest-common-denominator layer would erase them, or make us pass them through raw,
   which is our own adapter again.
4. **Five adapter targets cover the whole list.** Most of the named providers speak Chat Completions.
   Claude on Bedrock and Vertex is served by the `anthropic` SDK's `AnthropicBedrock` / `AnthropicVertex`
   clients, so it's a variant of the Anthropic adapter. Feature parity (server tools, some betas) differs by
   platform and is declared in Capabilities.

LiteLLM stays a possible later opt-in adapter for a long-tail provider. If we ever add it, it goes behind an
optional extra, pinned with hashes (`uv` lockfile with `--require-hashes`), with
`LITELLM_LOCAL_MODEL_COST_MAP=True`, and its cost estimate never replaces our registry.

### 2. A provider-neutral internal model (`brain/providers/types.py`)

Plain dataclasses or Pydantic models. Field names follow Vercel and Pydantic AI where they overlap.

```python
Role = Literal["user", "assistant"]          # system is separate; tool results are user-side parts

@dataclass
class Text:        text: str; citations: list[Citation] = []; provider_data: dict | None = None
@dataclass
class Reasoning:   text: str; provider_data: dict | None = None          # e.g. Anthropic thinking signature
@dataclass
class ToolCall:    id: str; name: str; args: dict; provider_data: dict | None = None
@dataclass
class ToolResult:  call_id: str; content: str | list[SourceDoc]; is_error: bool = False
@dataclass
class HostedToolCall:   id: str; kind: str; args: dict; provider_data: dict | None = None  # server-side web_search…
@dataclass
class HostedToolResult: call_id: str; kind: str; sources: list[SourceDoc]; error: str | None; provider_data: dict | None = None
ContentPart = Text | Reasoning | ToolCall | ToolResult | HostedToolCall | HostedToolResult

@dataclass
class SourceDoc:   id: str; title: str; text: str; kind: str   # id = "ava://memory/…" | "ava://cal/…" | a URL; kind = memory|message|calendar|web
@dataclass
class Citation:    source_id: str; title: str = ""; cited_text: str = ""; kind: str = ""; verified: bool = True

@dataclass
class Message:     role: Role; parts: list[ContentPart]
@dataclass
class SystemBlock: text: str; cache: bool = False               # unchanged; adapters map `cache`

@dataclass
class ToolSpec:    name: str; description: str; input_schema: dict    # MCP-style inputSchema (JSON Schema)
@dataclass
class HostedTool:  kind: Literal["web_search", "web_fetch"]; max_uses: int

StopReason = Literal["end", "tool_calls", "pause", "max_tokens", "refusal", "error"]

@dataclass
class Usage:
    input_tokens: int = 0; output_tokens: int = 0           # output includes reasoning tokens
    cache_read_tokens: int = 0; cache_write_tokens: int = 0
    reasoning_tokens: int = 0                              # informational; already inside output_tokens
    hosted_tool_calls: dict[str, int] = {}                 # {"web_search": 2}
    reported_cost_usd: Decimal | None = None               # when the provider says what it charged (OpenRouter)
    extras: dict = {}                                       # e.g. Anthropic's 5m/1h cache-write split
    known: bool = True                                      # False when the provider sent no usage

@dataclass
class StepResult:  parts: list[ContentPart]; stop: StopReason; usage: Usage; cost_usd: Decimal; raw_stop: str

@dataclass(frozen=True)
class Capabilities:
    tools: bool; parallel_tools: bool; streaming: bool; reasoning: bool
    native_citations: bool; server_web_search: bool; server_web_fetch: bool
    structured_output: Literal["json_schema", "json_mode", "none"]
    prompt_caching: Literal["explicit", "automatic", "none"]; vision: bool
    max_output_tokens_bounds_reasoning: bool               # see 7
```

**`provider_data` is the rule that keeps Claude exact.** The adapter that produced a part stores its raw
block there (a thinking block's signature, a `server_tool_use` block, a `web_search_tool_result` with its
encrypted content). On the next step the same adapter re-emits that raw block byte for byte. Any other
adapter drops it and uses only the neutral fields. Within a turn the provider never changes. Between turns,
history is rebuilt from the text event log (`turn.build_messages`), so changing provider mid-conversation is
safe.

Stream events (`OnEvent` becomes typed):
`TextDelta(text)`, `ReasoningDelta(text)`, `ToolCallStarted(id, name)`, `ToolCallReady(ToolCall)`,
`HostedToolStarted(HostedToolCall)`, `HostedToolFinished(HostedToolResult)`, `PartDone(part)`. The loop never
sees a vendor block type name. `tainted` becomes "a `SourceDoc` of kind `web` entered this turn",
whichever way it arrived.

### 3. The `LLMProvider` interface (`brain/providers/base.py`)

```python
class LLMProvider(Protocol):
    id: str                                   # the configured provider instance, e.g. "anthropic", "azure-prod"
    kind: str                                 # adapter: anthropic | openai_compat | fake
    def capabilities(self, model: str) -> Capabilities: ...
    def step(self, *, purpose, model, system: list[SystemBlock], messages: list[Message],
             tools: list[ToolSpec], hosted: list[HostedTool], max_tokens: int,
             effort: Literal["low", "medium", "high"] | None = None,
             on_event: Callable[[StreamEvent], None] | None = None) -> StepResult: ...
    def complete(self, *, purpose, model, system, messages: list[Message], max_tokens: int,
                 output_model: type[BaseModel] | None = None, effort=None) -> LLMResult: ...
    def check(self) -> CheckReport: ...       # "test connection": cheap auth check + model list
```

`stream` is `step` with `on_event` set, as today. Every adapter goes through the same budget path: reserve
the worst case, call, settle from `Usage`, `fail` on exception. That lives in a shared `metered()` helper in
`base.py`, not in each adapter, so a new adapter can't skip it.

`Agent.llm` becomes a `Brain`. It resolves a role (`chat` / `triage` / `deep`) to `(provider, model)`, and
has `ready` (replacing `uses_real_model`) and `capabilities(role)`.

### 4. Adapters

**Anthropic** (`brain/providers/anthropic.py`). `ClaudeLLM` moves here unchanged in behaviour. Neutral to
wire: `ToolSpec` becomes `{name, description, input_schema}`. `HostedTool(web_search)` becomes
`{type: web_search_20250305, name, max_uses}` and `HostedTool(web_fetch)` adds `citations.enabled`.
`ToolResult` with `SourceDoc`s becomes `search_result` blocks. `SystemBlock.cache` becomes `cache_control`.
`effort` goes in `output_config.effort`. Structured output uses `output_config.format` + `transform_schema`,
and adaptive thinking is used when streaming. Wire to neutral: `search_result_location` /
`web_search_result_location` / `char_location` citations become `Citation` (`verified=True`), and `pause_turn`
becomes `stop="pause"`. Variants: `AnthropicBedrock` and `AnthropicVertex` clients, each with its own
Capabilities (server tools may be unavailable on a platform; the adapter then reports
`server_web_search=False` and section 6 applies).

**Acceptance gate:** a golden test records the exact kwargs today's `ClaudeLLM.step` / `complete` pass to
`messages.create` / `messages.stream` for every scenario in `tests/pipeline/test_loop.py` and `tests/app/test_stream.py`.
The new adapter must produce identical kwargs, and identical rendered text and sources from the same
responses.

**OpenAI-compatible** (`brain/providers/openai_compat.py`), on `openai.OpenAI(base_url=…, api_key=…)` with
**Chat Completions first** (the dialect every target shares; Responses can come later for OpenAI or Azure
hosted tools and reasoning summaries). Mapping: `ToolSpec` becomes `{"type": "function", "function": {name,
description, parameters}}`; `tool_calls` become `ToolCall` (with `arguments` JSON parsed, and a parse failure
returned to the model as an error result); each `ToolResult` becomes a `role: tool` message; `SourceDoc`s are
rendered as text with markers (section 5); `effort` becomes `reasoning_effort` where the model supports it;
`max_tokens` becomes `max_completion_tokens`; streaming always sends `stream_options.include_usage`;
`reasoning_content` / `reasoning` deltas (DeepSeek, OpenRouter, Ollama) become `ReasoningDelta`; and
`finish_reason` values `stop`, `tool_calls`, `length` and `content_filter` map onto `StopReason`. Structured
output uses `response_format: json_schema` (strict) where supported, with a strict-schema transform
(`additionalProperties: false`, every property required, optional fields as nullable). Otherwise it uses JSON
mode, otherwise instructions; it then validates, retries once with the error, and refuses after that.

Per-preset quirks live in a small **preset table** (data, not subclasses): default base URL, auth header
(`api-key` for Azure keys, bearer otherwise), whether `parallel_tool_calls` is accepted, schema transform,
whether usage reports `cost`, and default Capabilities. Presets: `openai`, `azure` (v1 `base_url`, key or
Entra token provider), `openrouter` (adds `usage.cost`; can pass `cache_control` to Anthropic models),
`xai`, `gemini` (OpenAI-compatible endpoint first; native adapter only if needed), `ollama`, `vllm`,
`custom`.

**Fake** (`brain/providers/fake.py`). It scripts neutral parts. A temporary `from_legacy()` translator accepts
today's Anthropic-shaped script dicts, so tests migrate file by file rather than in one sweep. The translator
is deleted once no test uses it. `demo_responder` stays the offline brain, with provider-neutral wording.

### 5. Citations without native support

1. Every source Ava gives the model (`recall`, calendar, our web tools) is a `SourceDoc` with a stable id
   (`ava://memory/<id>`, `ava://msg/<provider>/<id>`, `ava://cal/<id>`, or the URL).
2. On a non-native provider, a tool result is rendered as
   `[source id=ava://memory/12 title="Flight to BLR"]\n<text>\n[/source]` blocks. A short added rule tells
   the model to cite with `[ava://memory/12]` right after the claim it supports.
3. `brain/citations.py` parses `[<id>]` markers from the final text, and **keeps only ids that a tool result
   in this turn actually returned**. It then optionally checks that any quoted span appears in that source's
   text (`verified`). Unknown or hallucinated ids are stripped from the text, and an audit event
   `citation_dropped` is written. Markers are replaced by neutral `Citation`s on the `Text` part.
4. `loop.render()` works only on neutral `Text.citations`, so numbering (`[1]`), quote folding and the
   `sources` payload sent to the app are identical on both paths. The owner rule "if nothing supports an
   answer, say so" is unchanged.

### 6. Web search without server tools

`HostedTool(kind)` is a request. Each adapter fulfils it **natively** if its Capabilities allow (Anthropic
server tools now; OpenAI Responses `web_search` or OpenRouter's web plugin later). Otherwise the runtime
**substitutes Superaide's own function tools**: `web_search` (a search API: Brave, Tavily or Exa; the key goes
in the vault, the price per call is in the registry, and each call reserves before it runs) and `web_fetch`
(httpx with the SSRF guard from section 9, size and time limits, HTML to text). Both return `SourceDoc(kind="web")`,
so citations and taint work the same way. The skill's `budget.per_use_usd` and the `research` bucket still
apply. **0006 amendment:** `Tool.server_tool` (an Anthropic type string) is replaced by
`hosted: "web_search" | "web_fetch"`. The loader accepts the old field for one release and maps
`web_search_20250305` to `web_search`. Community skills still may not use hosted tools.

### 7. Pricing and the budget per provider and model

- `brain/pricing.py` becomes a **price registry** keyed by `(provider_kind, model)`. Each entry has input,
  output, cache-read and cache-write rates, an optional long-context tier, and per-call fees for hosted tools
  (`web_search: 0.01`). The built-in table covers Anthropic (today's prices) and the main OpenAI models. Each
  table carries a "checked on" date and a source URL.
- **Unknown price means refused**, as today, unless the owner sets one. Owner prices are stored in a
  `model_price` table (tenant/agent ids, set by, set at), shown in the UI, and audited.
- **Local is free only when it is really local.** A `ollama` / `vllm` / `custom` provider is priced at 0
  only if its `base_url` resolves to loopback or a private address. A "local" preset pointed at a public host
  needs an owner price like any other.
- **Worst-case reservation stays tokenizer-agnostic** (chars/2 plus overhead, the full output budget). It uses
  the cache-write multiplier only where caching is `explicit`, and adds `max_uses x fee` for hosted tools.
  This is only sound if `max_tokens` bounds reasoning too: on OpenAI-compatible we send
  `max_completion_tokens`. A preset where the limit doesn't bound reasoning
  (`max_output_tokens_bounds_reasoning=False`) is refused for roles with reasoning on, until we can cap it.
- **Settlement:** we compute cost from `Usage`. If the provider reports a cost (OpenRouter), we settle at the
  higher of the two and audit any difference over 5%. **If usage is missing (`known=False`), we settle at the
  reservation, never at 0.**
- `SpendEntry` gains a `provider` column. The model change and its Alembic revision land in the same
  package, because tests use `create_all` and wouldn't notice a missing migration.

### 8. Model roles

`chat`, `triage` and `deep` each map to **`(provider_id, model, effort)`**, so triage can run on a local
model while chat stays on Claude. The UI starts with "one provider for everything" plus an "advanced:
per-role" toggle. `config.py`'s `model_chat` / `model_triage` / `model_deep` remain as `.env` defaults for
the `anthropic` provider.

### 9. Config storage and security

- Table `llm_provider`: `id` (slug), `kind` (adapter), `preset`, `label`, `base_url`, `options` (non-secret
  JSON validated by the preset's `config_schema`), `enabled`, timestamps, `tenant_id` / `agent_id`.
  Table `model_role`: `role`, `provider_id`, `model`, `effort`. Owner prices go in `model_price`.
- Secrets stay in the vault (0004): slot `llm/<provider_id>/api_key`, AES-256-GCM with the slot as
  associated data, shown only as a masked hint, and never returned, logged or put in an audit row.
- `base_url` rules: https required unless the host is loopback; no userinfo, query or fragment; link-local
  and metadata addresses (169.254.0.0/16, fd00:ec2::254) refused always; private ranges allowed only for
  `ollama` / `vllm` / `custom`. The address is resolved and checked at save time and again on each connect,
  against DNS rebinding.
- Every change audits `provider_saved` / `provider_removed` / `role_changed` with field names, never values.

### 10. The config page ("Brain", under Settings)

1. **Provider list:** cards for Claude (Anthropic), OpenAI, Azure OpenAI / AI Foundry, OpenRouter, xAI,
   Gemini, Ollama / local, Custom (OpenAI-compatible), each with a short "where to get a key" guide (like
   today's `GUIDES`).
2. **Form from the preset's `config_schema`:** API key (password field, hint after save), base URL (prefilled,
   editable for Azure, local and custom), and preset options (Azure deployment names, for example).
3. **Test connection:** a cheap authenticated model list where the API has one. An optional "check tools"
   probe makes one tiny tool call, which is priced, reserved and shown as "about US$0.001". It reports
   reachable, key accepted, models found and tool calling working.
4. **Model pickers** for chat / triage / deep, populated from the model list and marked "priced" or "needs a
   price". Picking an unpriced remote model asks for input and output prices per million tokens.
5. **Capability warnings,** in plain words: "This model can't call tools, so Ava can't look things up or set
   reminders here." "No native citations: Ava's sources are checked by Superaide instead." "Web search will use
   Superaide's search (needs a search key)." "Local model: free, but slower and less reliable with tools."
6. Responses never include a secret. `POST` endpoints use the existing CSRF and owner checks.

### 11. One plugin pattern for providers, channels, connectors and skills

One small registry (`superaide/plugins/registry.py`), generic over the plugin kind:

```python
class PluginManifest(BaseModel):     # same field names as 0006's superaide.json where they overlap
    id: str; kind: Literal["provider", "channel", "connector", "skill"]; version: str
    publisher: str; tier: Literal["official", "community"]; title: str; summary: str
    config_schema: dict              # JSON Schema; secret fields marked "x-secret": true -> vault
    network: list[str] = []          # hosts it may reach
    capabilities: dict = {}          # kind-specific (provider Capabilities, channel features)

class Plugin(Protocol):
    manifest: PluginManifest
    def check(self, config, secrets) -> CheckReport: ...   # replaces vault/check.py's check_*

class Registry(Generic[P]):
    def register(self, plugin: P) -> None: ...
    def get(self, id: str) -> P: ...
    def all(self) -> list[P]: ...
```

- **Code plugins** (providers, channels, connectors) are built in and registered in code. A deployment can
  add more through Python entry points (`superaide.providers`, `superaide.channels`,
  `superaide.connectors`), **loaded only if their names are on an operator allow-list in config**: entry
  points run code at import, so they are an operator decision, never something an owner installs.
- Kind-specific interfaces sit on top: `ProviderPlugin.build(config, secrets) -> LLMProvider`;
  `ChannelPlugin.messenger(...)` and `.listener(...)` (`ChannelRunner` loops over enabled channel plugins
  instead of naming Telegram and Slack); `ConnectorPlugin` with OAuth metadata (the Google PKCE flow).
- `vault/store.FIELDS` and the per-service routes are derived from manifests (`x-secret` fields go to vault
  slots), and the settings UI renders forms from `config_schema`.
- **Skills keep 0006 unchanged.** They are folders (`SKILL.md` + `superaide.json`) with no code of ours, and
  `skills/registry.catalog()` becomes the skill kind of the same `Registry`. A skill's `connectors[].provider`
  must name a registered connector plugin.

## Migration (each step ships on its own, with tests green)

1. **M1, no behaviour change (Anthropic only).** Add neutral types, `LLMProvider`, `metered()`, the Anthropic
   adapter and the Fake with its legacy translator. Neutralise `loop.py`, `render()`, `tools_for`,
   `calendar.py` and `recall` results. Turn pricing into a registry and add `SpendEntry.provider` with its
   migration. Replace `uses_real_model` with `brain.ready`. Gate: the golden wire test plus the full suite.
2. **M2, provider config.** Add the `llm_provider` / `model_role` / `model_price` tables and migration. A data
   migration turns an existing `anthropic/api_key` vault row into an `anthropic` provider with roles from
   `.env`; `ANTHROPIC_API_KEY` still works as a fallback, and the owner does nothing. Add `app/providers.py`
   routes and (Superseded: `POST /app/connections/anthropic` was removed; the AI model page and `/app/providers` are the only place to connect a provider.)
3. **M3, OpenAI-compatible adapter** with presets, the citation marker parser and verifier, and structured
   output fallback. Add the frontend Brain page.
4. **M4, Superaide's own web search and fetch** for providers without hosted search, plus the 0006 manifest
   change.
5. **M5, plugin registry:** port channels and connectors and derive `FIELDS`. Later: Bedrock and Vertex
   variants, Responses API, a native Gemini adapter only if the compatibility endpoint falls short.

## Implementation plan: work packages

Packages can run in parallel when their files don't overlap. **WP0 goes first** (it is the contract); after
it, packages in the same wave run in parallel.

| WP | Wave | Owns (files) | Delivers | Tests |
| --- | --- | --- | --- | --- |
| WP0 Contract | 0 | `brain/providers/{__init__,types,base,fake}.py` | Neutral types, `LLMProvider`, `StreamEvent`, `metered()` budget wrapper, `FakeProvider` + `from_legacy()` | Unit tests for types and the fake; `metered()` reserves/settles/fails, settles at reservation when `usage.known=False` |
| WP1 Anthropic adapter | 1 | `brain/providers/anthropic.py`, `brain/llm.py` (becomes a re-export shim), `cli.py` (`hello`) | Lossless adapter with `provider_data` round-trip; Bedrock/Vertex client hooks | **Golden kwargs test** against recorded `ClaudeLLM` requests; stream event mapping; thinking and server-tool blocks re-emitted byte-identical |
| WP2 Loop neutralisation | 1 | `brain/loop.py`, `brain/turn.py`, `skills/registry.py` (`tools_for`), `skills/calendar.py`, `pipeline.py` (`_owner_turn`, `_think`) | Loop on `Message`/`ContentPart`; `render()` on `Citation`; taint from `SourceDoc.kind`; `HostedTool` requests | Existing `test_loop.py` / `test_stream.py` / `test_skills.py` pass via the fake (migrated to neutral scripts); new test: taint from a web `SourceDoc` |
| WP3 Price registry + ledger | 1 | `brain/pricing.py`, `brain/budget.py`, `db/models.py` (`SpendEntry.provider`, `ModelPrice`), `alembic/versions/<rev>_provider_pricing.py` | Registry by `(provider_kind, model)`; owner overrides; local=0 rule; reported-cost settlement; hosted-tool fees | Unknown price refused; owner price accepted; loopback base URL priced 0 and public "ollama" refused; settle at higher of computed/reported; Alembic upgrade test |
| WP4 Readiness | 1 | `factory.py`, `app/routes.py`, `api/admin.py`, `knowledge/{facts,ingest,learn}.py` | `Brain` with `ready` / `capabilities(role)` replaces `uses_real_model`; neutral offline wording | `test_factory.py`, `test_learn.py`, `test_intro.py` updated; offline still costs nothing |
| WP5 Provider config | 2 | `db/models.py` (new tables only, after WP3 merges), `alembic/versions/<rev>_llm_providers.py`, `app/providers.py`, `vault/store.py` (slot helpers only), `main.py` (router mount), `security/urls.py` | Tables, data migration of the Anthropic key, CRUD + test-connection + roles routes, SSRF-safe URL validator, rebuild-on-change | Never echoes keys; CSRF/owner-only; URL validator table (metadata IP, userinfo, http non-loopback, rebinding); migration moves an existing key; env fallback |
| WP6 OpenAI-compatible | 2 | `brain/providers/openai_compat.py`, `brain/providers/presets.py`, `brain/citations.py`, `pyproject.toml` (`openai`) | Chat Completions adapter, presets, strict-schema transform, structured-output fallback, marker parser/verifier | `httpx.MockTransport` fixtures per preset (tool calls, parallel calls, streaming with usage, missing usage, `length`, reasoning deltas, OpenRouter `cost`); citation verifier drops unknown ids and audits |
| WP7 Brain page | 2 | `frontend/src/features/settings/Brain*.tsx`, `frontend/src/lib/api.ts` (provider calls), `Connections.tsx` (link to the new page, Claude card removed) | Provider cards, schema-driven form, test connection, role pickers, price prompt, capability warnings | Component tests; Playwright E2E: connect a fake provider, test connection, pick models, error states, key never shown |
| WP8 Own web tools | 3 | `tools/web_search.py`, `tools/web_fetch.py`, `skills/manifest.py` (`hosted`), `skills/builtin/web-research/*`, `skills/manifest.schema.json` | Search API backend, SSRF-guarded fetch, per-call reservation, 0006 amendment | Old `server_tool` manifests still load; fetch refuses private/metadata hosts; each search reserves; results taint the turn |
| WP9 Plugin registry | 4 | `plugins/registry.py`, `channels/runner.py`, `channels/{telegram,slack}.py` (plugin wrappers), `connectors/google.py`, `vault/check.py`, `vault/store.py` (`FIELDS` derived), `app/connections.py` | One `Registry`; channels/connectors/providers as plugins; entry-point allow-list | `test_channel_runner.py`, `test_connections.py`, `test_vault_check.py` unchanged in behaviour; an un-allow-listed entry point is not loaded |

Conflict notes: `db/models.py` is touched by WP3 then WP5, so WP5 rebases after WP3 merges (two separate
Alembic revisions, chained). `vault/store.py` is touched by WP5 (slot helpers) then WP9 (FIELDS), in that
order. `app/connections.py` belongs only to WP9; WP5 adds a new `app/providers.py` and leaves the
`/anthropic` route as an alias until WP9.

## Risks

- **Regressing Claude's cited answers.** Mitigated by the golden wire test and `provider_data` round-trip.
  M1 changes no behaviour.
- **Budget soundness on new providers:** reasoning tokens not bounded, usage missing in streams, stale or
  wrong prices. Mitigated by `max_completion_tokens`, settling at the reservation when usage is missing,
  refusing unknown prices, dated price tables and OpenRouter cost cross-checks. Price tables still need
  keeping up to date.
- **"OpenAI-compatible" drift:** quirks differ by vendor and change. Mitigated by the preset table and
  per-preset fixtures, and by test connection with a tool probe before a model is used.
- **Weaker tool use and citation discipline on small or local models.** Mitigated by capability warnings, the
  citation verifier (no unverified source is ever shown), and charter rules stated provider-neutrally.
  Quality will still vary.
- **SSRF and key leakage through owner-entered URLs.** Mitigated by the URL validator, rebinding checks and
  never echoing keys. The owner can still send their own key to a host they chose; the UI says so.
- **Prompt injection through web content** is unchanged: web `SourceDoc`s taint the turn on every provider.
- **Supply chain:** one new dependency (`openai`), pinned in `uv.lock`, with Dependabot alerts.

## Consequences

- Changing Ava's model is a settings change, not a code change, for any OpenAI-compatible service.
- Claude keeps its full feature set; other providers get the same cited, budgeted, approval-gated loop with
  graceful degradation that the UI explains.
- Adding a provider, channel or connector means writing one plugin with a manifest. Adding a skill still
  means adding a folder (0006).
