# Industry and open-source memory scoping models (as of Oct 2026)

Research depth note: roughly 20 tool calls. Several sub-questions (Rewind/Limitless/Pieces, Poke/Lindy/Sintra/Kin, LangMem/Cognee/Memobase/A-Mem, MSC, thread linking) were NOT covered and are listed under Gaps. Some OpenAI help-center pages returned HTTP 403, so OpenAI claims rely on search-result summaries.

## 1. Consumer and enterprise assistant memory: scoping units (ChatGPT, Claude, Gemini, Copilot)

### Takeaway
Every major product scopes memory to the individual account first, then offers a narrower container (project) and an opt-out (incognito/temporary/off). Shared contexts (group chats, shared projects, org spaces) are deliberately kept apart from personal memory. None of these is a model for one owner plus other people talking to the same assistant.

### Cited Findings
- Claude: each project has its own memory; inside a project Claude uses that project's memory plus non-project memory, but not other projects' memory. Past-chat search is similarly bounded (non-project chats, or within one project). "Use account memory" can be turned off per project for full separation. — [Claude Help Center](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-conversations)
- Claude incognito chats are not saved to memory or history and are not pulled into searches; on Team/Enterprise they still appear in exports and follow retention policies. Memory can be disabled for a single chat but cannot be changed once the chat starts. — same source
- Claude Team/Enterprise: memory is off for each member until they turn it on; owners cannot view or edit individual memories; turning org memory off permanently deletes everyone's memory; individual member edits are not audit-logged. — same source
- ChatGPT group chats: personal memory is not used in group chats and no new memories are created from them; account-level memory and custom instructions stay out; groups can have their own custom instructions; OpenAI says it is exploring finer controls. — [OpenAI help, group chats](https://help.openai.com/en/articles/12703475-group-chats-in-chatgpt) and [OpenAI announcement](https://openai.com/index/group-chats-in-chatgpt/) (via search-result summary; page fetch returned 403)
- ChatGPT project-only memory (Aug 2025): the project draws context only from chats in that project and general saved memories do not affect it; chosen at project creation and not changeable later; requires saved-memory and chat-history settings enabled. Sources conflict on plan availability (OpenAI Academy says Enterprise/Business with workspace memory on; press does not state this). Users reported occasional leakage from general memory. — [TechRadar](https://www.techradar.com/ai-platforms-assistants/chatgpt/chatgpt-project-only-memory-is-live-and-it-might-change-how-you-work-with-ai), [OpenAI Academy](https://academy.openai.com/public/clubs/work-users-ynjqu/resources/projects)
- ChatGPT shared-project memory behaviour for collaborators: not found in any source.
- Gemini Personal Intelligence uses custom instructions, past chats and connected Google apps, tries to show which sources it used, and can be disabled per service or per prompt. Beta, excluded in EEA/UK/Switzerland; work/school accounts reportedly get no memory (third-party blog). — [Google Gemini personalization](https://gemini.google/overview/personalization/?hl=en), [MemoryPlugin blog (third party)](https://blog.memoryplugin.com/how-gemini-memory-works/)
- Microsoft 365 Copilot: two layers, a tenant-level "Enhanced personalization" admin switch (on by default) and user toggles for custom instructions, saved memories and chat history. Purview retention policies do not cover memory; no admin retention controls for Copilot memory. — [Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-365/copilot/copilot-personalization-memory)

### Inferences
- The universal pattern is: scope = (account) with optional sub-scope (project) and hard exclusion of personal memory from multi-person contexts. ChatGPT group chats are the closest documented analogue to "assistant talking to several people", and the vendor chose to exclude personal memory entirely rather than filter it. (Inference from the documented behaviour.)
- Per-chat memory choice being immutable after the first message (Claude) is a useful design precedent: the memory policy of a conversation is fixed at creation.

### Gaps
- No official OpenAI memory FAQ text (403). No documented sharing semantics for shared projects. Gemini official privacy/retention details not retrieved.

## 2. Slack: what an app or agent can read

### Takeaway
Slack enforces access by token type and scope: bot tokens see what the bot is in or is invoked on; user tokens see what that user sees. Mention events give narrow, invocation-scoped access; history events give ambient access. Real-time Search is permission-enforced at query time.

### Cited Findings
- Slack MCP server uses user-token scopes: search.read.{public,private,mpim,im}, history scopes (channels/groups/mpim/im), chat:write, etc. Workspace admins approve MCP clients; activity is auditable; only Marketplace-published and internal apps can use MCP. — [Slack MCP docs](https://docs.slack.dev/ai/slack-mcp-server/)
- assistant.search.context: bot tokens require an action_token obtained from message or app_mention event payloads; user tokens do not need one. For private conversation data the app needs a user token (xoxp-) via OAuth with at least search:read.public. Results include context_messages; follow up with conversations.replies (threads) or conversations.history. Max 20 results per page. — [Slack Real-time Search / assistant.search.context](https://docs.slack.dev/reference/methods/assistant.search.context/), [RTS API](https://docs.slack.dev/apis/web-api/real-time-search-api)
- Agent context: with agent_view and app_context_changed, the agent learns which channel/DM/canvas the user has open; this context also appears on message.im and app_home_opened. — [Slack agent context management](https://docs.slack.dev/ai/agent-context-management)
- Event granularity: app_mention needs only app_mentions:read and delivers only mentions; message.channels (channels:history) and message.groups deliver all messages in those channels. — [message.channels](https://docs.slack.dev/reference/events/message.channels). Claim that app_mention works without channel membership is from a third-party doc ([Guild](https://docs.guild.ai/services/slack)) and is unverified.
- Dust: Slack connection syncs messages/threads of selected channels, excluding bot messages, DMs and private channels unless the bot is invited (private indexing needs a flag). Spaces are open or restricted; an agent using a restricted space is available only to its members. In a Slack channel, an agent's answer is visible to everyone in that channel. — [Dust Slack connection](https://docs.dust.tt/docs/user-documentation/admins/connections-management/slack), [Dust access controls](https://docs.dust.tt/docs/access-controls-and-permissions)

### Inferences
- Slack's design makes the audience of a reply equal to the channel membership, so an agent that mixes in a person's private memory when replying in a channel leaks it. Dust's note (answer visible to all in channel) shows the same risk is recognised. (Inference.)
- Action-token requirement ties bot-side search to a triggering human event, a way of binding retrieval to "who invoked me".

### Gaps
- Slack Governance and trust page, exact scope list for im:history on bots, and Slack AI's own internal memory/scoping not retrieved. Glean, Lindy, Poke, Sintra, Kin not researched.

## 3. Open-source memory frameworks

### Takeaway
Mem0 and Graphiti provide namespaces (ids), not access control. Mem0 attributes facts by speaker role into user_id vs agent_id; Graphiti namespaces by group_id and explicitly says the filter is not authorization. None natively models audience-scoped facts about one person told to different people.

### Cited Findings
- Mem0 identifiers: user_id (persistent person), agent_id (persona), app_id (deployment/tenant), run_id (short-lived flow). On the default extraction path facts from user messages get user_id and from assistant messages agent_id; a record usually has one or the other. Unspecified filter fields are unconstrained (a user_id-only filter also returns records with other ids set); AND of user_id and agent_id returns nothing for default records; misspelled ids silently return nothing. Graph entities are separate from scope ids. — [Mem0 entity-scoped memory docs](https://docs.mem0.ai/platform/features/entity-scoped-memory)
- Mem0 paper: claims 26% relative gain over OpenAI memory (LLM-judge on LoCoMo), ~91% lower p95 latency and >90% token cost reduction vs full context; graph variant ~2% better. Vendor-authored. — [arXiv 2504.19413](https://arxiv.org/abs/2504.19413)
- Graphiti/Zep: three subgraphs (episodes as non-lossy raw input, resolved semantic entities and fact edges, communities). Bi-temporal: t_valid/t_invalid (world time) and t'_created/t'_expired (ingestion time); contradicting edges get invalidated, favouring new information. Entity resolution = embedding + full-text candidate search, then LLM decides duplicates. — [Zep paper](https://arxiv.org/html/2501.13956)
- Graphiti group_id: nodes and edges sharing a group_id form an independent graph; searches take group_ids; "a namespace filter does not replace application authorization"; derive ids from authenticated state; too many namespaces fragment data. — [Graphiti namespacing docs](https://help.getzep.com/graphiti/core-concepts/graph-namespacing)
- Zep results: DMR 94.8% vs 93.4% MemGPT; LongMemEval gpt-4o 71.2% vs 60.2% full-context (1.6k vs 115k context tokens, 2.58s vs 28.9s); gpt-4o-mini 63.8% vs 55.4%. — [Zep paper](https://arxiv.org/html/2501.13956)
- Letta: memory blocks are always-in-context sections with label/description/value/limit, editable by the agent via tools; one block can be attached to several agents (shared, updates propagate), can be read_only; concurrent writes are last-write-wins. — [Letta memory blocks docs](https://docs.letta.com/guides/core-concepts/memory/memory-blocks)

### Inferences
- Mem0's speaker-based split (user vs agent) is a role split, not a person/audience split; per-person memory in a multi-party setting needs one user_id per human, plus an owner-level scope the application must enforce. Graphiti's bi-temporal edges suit tracking "owner's fact changed", and episodes provide provenance back to the source message/channel, which could carry audience tags (application-side). None of this exists out of the box. (Inference.)
- Read-only shared Letta blocks resemble a "policy/owner brief" that several personas read but cannot write.

### Gaps
- LangMem, Cognee, Memobase, A-Mem, Letta archival memory, Zep's user/thread/session model not researched; no first-party multi-party semantics found for any open-source system.

## 4. Group-chat and multi-party assistants, design write-ups

### Takeaway
Little published design guidance exists for assistants with per-speaker memory; the vendor docs found show channel-visible answers and personal-memory exclusion. Multi-party benchmarks show current memory systems do poorly.

### Cited Findings
- GroupMemBench (May 2026): questions tied to a specific asker; best memory system averages 46.0%; knowledge update 27.1%, term ambiguity 37.7%; a BM25 baseline matches or beats most memory systems; authors say ingestion discards structural and lexical cues group memory needs. — [arXiv 2605.14498](https://arxiv.org/abs/2605.14498)
- EverMemBench (Feb 2026): multi-party multi-group conversations over 1M tokens; multi-hop reasoning reaches only 26% even with oracle evidence; similarity retrieval misses implicitly relevant memories; temporal reasoning needs version semantics. QA count differs across versions (1,000+ vs 2,400). — [arXiv 2602.01313](https://arxiv.org/abs/2602.01313v2)

### Inferences
- Because BM25 competes with fancy memory, keeping speaker, channel and thread metadata as indexed fields (hybrid lexical plus vector) is likely worth more than heavy consolidation. (Inference from GroupMemBench's conclusion.)

### Gaps
- No post-mortems found for Dust, Glean, Lindy, Poke, Sintra, Kin or Telegram group bots; not searched in depth.

## 5. Benchmarks for long-term memory

### Takeaway
LongMemEval is the more robust single-user benchmark; LoCoMo scores are contested and non-reproducible across vendors. Retrieval with fact-augmented indexes and time-aware queries beats full context on cost and often accuracy.

### Cited Findings
- LongMemEval: 500 questions; abilities = information extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention; commercial assistants and long-context LLMs show ~30% accuracy drop; recommended session decomposition, fact-augmented keys and time-aware query expansion. — [arXiv 2410.10813](https://arxiv.org/abs/2410.10813)
- LoCoMo disputes: Zep's corrected LoCoMo score 75.14% after a category-5 denominator error; reported Letta numbers range 74.0 to 83.2 and Mem0 68.5 to 91.6 depending on source; critiques say LoCoMo tests short-range retrieval and largely ignores knowledge updates. Mostly vendor blogs and secondary commentary, no independent reproduction found. — [Zep blog](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/), [Towards AI / memoryplugin summaries](https://www.memoryplugin.com/wiki/evaluating.html)
- Cost: Zep 1.6k vs 115k average context tokens ([Zep paper](https://arxiv.org/html/2501.13956)); Mem0 reports >90% token reduction vs full context ([arXiv 2504.19413](https://arxiv.org/abs/2504.19413)).

### Inferences
- Treat vendor-reported LoCoMo numbers as unreliable for choosing a system; run LongMemEval-style tests plus a custom audience-leak test set.

### Gaps
- MSC, MemoryAgentBench, MPC datasets not researched; no 2025-26 leaderboard verified independently.

## 6. Thread and topic linking across DM and channel

### Takeaway
Not researched in this pass; no evidence collected.

### Cited Findings
- Slack provides the primitives only: conversations.replies for threads and search context_messages — [Slack agent context management](https://docs.slack.dev/ai/agent-context-management)

### Inferences
- Likely linkable keys (ticket ids, URLs, entity names, embeddings) are plausible but unverified; Graphiti entity resolution (embedding plus full-text plus LLM) is a documented example of the entity-merge approach ([Zep paper](https://arxiv.org/html/2501.13956)).

### Gaps
- No product write-up or evaluation found on linking a DM discussion to a channel discussion. Rewind/Limitless/Pieces scoping not researched.
