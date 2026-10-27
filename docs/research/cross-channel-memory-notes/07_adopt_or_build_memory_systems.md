# Adopt, borrow or ignore: existing agent-memory systems vs Superaide's audience-scoped, multi-party memory

Research date 2026-10-23. Evidence tags: [P] primary source read (paper text, official docs, or code cloned and read); [V] vendor-reported number or claim (not independently verified); [U] could not verify from a primary source.
Code clones are shallow clones from 2026-10-23 into the session scratchpad (`.../scratchpad/r/<repo>`). Paper text for 2504.19413, 2501.13956, 2502.12110, 2405.14831, 2304.03442, 2310.08560 was read as PDF-to-text extractions of the arXiv PDFs (the WebFetch summariser could not decode PDFs; I read the extracted text with grep/sed, so some paragraphs were read and others only keyword-scanned; see the coverage log).

## 0. Source coverage log (what I read, what I could not)

### Takeaway
Primary sources were read for Mem0 (paper + code + docs), Graphiti/Zep (paper + code), Anthropic memory tool (full doc), Letta (docs page + repo README only), OpenAI sessions (docs page), Cognee/Memobase/LangMem/Supermemory/MemOS (partial: repo layout, selected files or README). Several items are not verified; each is flagged below.

### Cited Findings
- Superaide side read from `origin/main` (read only): `docs/decisions/0008-one-ava-across-channels.md` (first ~150 lines), `backend/src/superaide/memory/audience.py` (full), `backend/src/superaide/memory/store.py` (first ~150 lines). Alembic file names show MemoryItem evolved through embeddings, importance, trust rank + supersession, episodes + consolidation runs, source documents, passport, source_ref (`backend/alembic/versions/0015, 0035, 0040, 0045, 0046, 0048, 0063, 0064, 0067`). I did NOT read `recall.py`, `merge.py`, `sleep.py`, `knowledge/erase.py`, or models; statements about them below are from the ADR text only.
- Mem0: read `mem0/mem0/memory/main.py` (scoping helper lines ~316-420, ADD/UPDATE/DELETE history calls), `mem0/mem0/vector_stores/pgvector.py` (filter builder + search), `mem0/mem0/configs/prompts.py` (ADDITIVE_EXTRACTION_PROMPT header), `docs/changelog/sdk.mdx`, the paper text (sections 2-4 and Tables 1-2), and https://docs.mem0.ai/platform/features/entity-scoped-memory. Repo version `2.2.1`, Apache-2.0, last commit 2026-10-20 [P].
- Graphiti: read `graphiti_core/utils/maintenance/edge_operations.py` (`resolve_edge_contradictions`), `edges.py` (field defs), `nodes.py` (group_id and delete), `search/search_utils.py` (group_id filtering), `helpers.py` (`validate_group_id`), `graphiti.py` (`remove_episode`), README (backend list), and the paper text (sections 2.1-2.2.3, Tables 1-3). Version `graphiti-core 0.31.2`, Apache-2.0, last commit 2026-10-23 [P].
- Letta: the cloned `letta-ai/letta` main contains only docs/README; its README says current source is `letta-ai/letta-code` and "the retired Letta V1 API server" lives on the `archive` branch [P: `scratchpad/r/letta/README.md`]. I did not read any Letta server code. Memory-block semantics are from https://docs.letta.com/guides/core-concepts/memory/memory-blocks [P docs, summarised by a fetch model, not verbatim].
- LangMem: read directory layout and namespace handling in `langmem/src/langmem/knowledge/extraction.py` (namespace tuples with `{langgraph_user_id}` templates); MIT; last commit 2026-10-02 [P]. I did not read the Postgres BaseStore implementation (it lives in LangGraph, not this repo) [U].
- Cognee: read layout, `graph/postgres_demo/__init__.py` header, `infrastructure/databases/provenance/source_refs.py` header; Apache-2.0; last commit 2026-10-16 [P]. Did not read the permission code beyond file names [U].
- Memobase: read `models/database.py` class names/pgvector columns and docker-compose; Apache-2.0; last commit 2026-01-11 (about 9 months stale) [P]. Did not read retrieval/profile code [U].
- Supermemory and MemOS: README-level only via https://github.com/supermemoryai/supermemory and https://github.com/MemTensor/MemOS (fetched through a summariser). No code, no schema read [U for all internals].
- Zep: https://github.com/getzep/zep README via summariser [P-ish]. Zep Cloud docs not read.
- A-Mem (2502.12110), HippoRAG (2405.14831), Generative Agents (2304.03442), MemGPT (2310.08560): keyword-scanned text extractions only (method names, key tables). I did NOT read their evaluation sections in full.
- Anthropic memory tool: full page https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool [P]. Context-editing page NOT read [U].
- OpenAI Agents SDK sessions: https://openai.github.io/openai-agents-python/sessions/ via summariser [P-ish].
- Slack MCP/RTS: https://docs.slack.dev/ai/slack-mcp-server/ (via summariser) and a previously saved copy of the RTS API page in the shared scratchpad (`scratchpad/p/rtsapi.txt`, fetched by a sibling researcher, quoted at lines 8, 369-371) [P-ish].
- Claude Agent SDK memory: NOT researched beyond the memory-tool doc [U].

### Inferences
- Treat anything marked "summariser" as reliable for gist but re-verify exact wording before quoting in a decision record.

### Gaps
- Not read: Letta server code, Cognee permissions code, Memobase retrieval, Supermemory/MemOS internals, LangGraph PostgresStore, Anthropic context-editing doc, Claude Agent SDK memory docs, Zep Cloud docs, the Superaide `recall.py`/`erase.py`/models.

---

## 1. Per-system verdict

### Takeaway
ADOPT nothing as the memory store: every candidate either lacks Postgres/RLS-compatible scoping, enforces scope by optional caller-supplied filters (fail-open), or needs a second database. BORROW IDEAS from Graphiti (bi-temporal edge invalidation, episode provenance with refcounted deletion), Mem0 (extract-then-reconcile against nearest neighbours, per-memory history log, linked_memory_ids), Cognee (refcounted source-ref deletion), Letta (pinned core blocks, read-only blocks), Park et al. (recency/importance/relevance scoring, reflection). IGNORE the rest as products.

### Cited Findings

**Mem0 (mem0ai/mem0, Apache-2.0, v2.2.1): BORROW IDEAS; do not adopt**
- Identifiers: `user_id`, `agent_id`, `run_id` are the only first-class scope keys in the OSS helper (`ENTITY_PARAMS = frozenset({"user_id","agent_id","run_id"})`, `main.py` line 135); add/search raise unless at least one is supplied (`main.py` ~393). `app_id` exists on the platform per docs, not in the OSS ENTITY_PARAMS [P: main.py; docs.mem0.ai entity-scoped-memory].
- Docs rules: "Unspecified fields aren't constrained. Filtering on `user_id` alone matches records that also have other fields set"; on the default path "each fact goes to the speaker who stated it, so a record gets `user_id` or `agent_id`, not both"; AND of user_id and agent_id returns nothing for default-path records [P: https://docs.mem0.ai/platform/features/entity-scoped-memory]. This is a single-person-or-agent owner model, not an audience (set of people allowed to see a fact) model.
- pgvector backend exists (`vector_stores/pgvector.py`): one table per collection, JSONB `payload`, filters translated to `payload->>%s = %s` (and `$or`, `$not`, `in`, `contains`); search is `ORDER BY vector <=> %s::vector LIMIT %s`. `_build_filter_conditions` returns no WHERE clause when `filters` is empty, i.e. scoping is whatever the caller passes (fail-open) [P: pgvector.py lines 50-120, 331-368]. No tenant_id column, no RLS, no audience-superset operator; arbitrary metadata is JSONB-only (no typed columns, no index guidance in the code I read).
- Update logic: the paper (Algorithm 1; section 2) has the LLM choose ADD / UPDATE / DELETE / NOOP per extracted fact after retrieving the top-s similar memories ("function-calling interface") [P: arXiv 2504.19413]. CURRENT OSS CODE HAS MOVED AWAY FROM THIS: changelog for the V3 pipeline: "Single-Pass Extraction: Replaced 2-LLM-call pipeline with additive extraction using `ADDITIVE_EXTRACTION_PROMPT`. Memories accumulate via `linked_memory_ids`: no more UPDATE/DELETE events"; `add()` returns ADD-only events [P: `mem0/docs/changelog/sdk.mdx` lines 375-388]. The old `DEFAULT_UPDATE_MEMORY_PROMPT` still sits in `configs/prompts.py` (line 176). So the paper's headline mechanism is not what the product ships today.
- History/audit: every ADD/UPDATE/DELETE writes a history row (`db.add_history(memory_id, old, new, event)` in `main.py` ~2094-2130); history lives in a separate `history` table created in `memory/storage.py` using SQLite by default [P]. This stores memory text separately from the vector store, which is an erasure hazard unless purged too (I did not confirm whether delete purges history rows [U]).
- Graph memory (Mem0g) is Neo4j-based in the paper; LLM resolver marks obsolete relations "invalid rather than physically removing them" [P: paper section 2.2]. A temporal-invalidation idea but no stated valid-from/to fields.
- Numbers (LOCoMo, GPT-4o-mini, authors' own run) [V]: Mem0 overall J 66.88 +- 0.15, Mem0g 68.44 +- 0.17, Zep 65.99 +- 0.16, LangMem 58.10, OpenAI memory 52.90, A-Mem 48.38, full-context 72.90 (Table 2). Mem0 search latency p50 0.148 s / p95 0.200 s; total p95 1.440 s vs full-context p95 17.117 s. Memory footprint: Mem0 about 7k tokens per conversation, Mem0g about 14k, Zep above 600k (sec 4.5). Retrieved context 1,764 tokens (Mem0) vs 26,031 (full) [P table; all V because authors of Mem0 ran competitors].
- Mem0's claim that Zep "often failed" immediately after ingest because of asynchronous graph processing is Mem0's observation of a competitor [V]; Zep's own paper reports different numbers on different benchmarks (below), so do not cross-compare.
- Cost: paper uses GPT-4o-mini for extraction and update; ADD-only V3 is one LLM call per add (per changelog) [P]. Every call is hidden inside `Memory.add`; to put it under a spend cap you must inject your own LLM class (Mem0 supports pluggable `llms/`), otherwise reservations are bypassed [P: directory listing; not tested].

**Zep / Graphiti (getzep/graphiti, Apache-2.0, v0.31.2): BORROW IDEAS (temporal model, episodes); do not adopt**
- Data model: episodes (raw messages/text/JSON), semantic entity nodes, fact edges, community nodes. Each node and edge carries a `group_id` ("partition of the graph", `nodes.py` line 96) [P].
- Bi-temporal edges: `created_at`, `expired_at` (system time T'), `valid_at`, `invalid_at` (event time T), plus `reference_time` (`edges.py` ~271-285) [P]. Paper: "tvalid and tinvalid ... track the temporal range during which facts held true... t'created and t'expired monitor when facts are created or invalidated in the system" [P: 2501.13956 sec 2.2.3].
- Invalidation algorithm in code (`edge_operations.py::resolve_edge_contradictions`, lines 748-783): candidate edges come from an LLM contradiction check; skip when the old edge already ended before the new one begins (or vice versa); otherwise if old.valid_at < new.valid_at then set `old.invalid_at = new.valid_at` and `old.expired_at = old.expired_at or utc_now()`. The old fact is kept, never deleted [P].
- Entity resolution: 1024-d embedding of entity name + full-text search over names/summaries, then an LLM resolution prompt; edge dedupe is restricted "to edges existing between the same entity pairs" [P: paper 2.2.1-2.2.2].
- Provenance: edges carry an `episodes` list; `Graphiti.remove_episode` deletes edges whose only supporting episode is the removed one, keeps edges still supported by other episodes, and deletes entity nodes mentioned only by that episode (`graphiti.py` 2174-2213) [P]. This is the refcounted deletion pattern Superaide needs for tombstoned Slack messages. Note it removes the edge but I did not see summaries of surviving entities being regenerated [U].
- Scoping is fail-open: `fulltext_query(query, group_ids, driver)` only adds the group filter `if group_ids is not None`; `validate_group_id` only checks character set (alphanumeric, dash, underscore) [P: search_utils.py 85-108; helpers.py 145-180]. Omit group_ids and the search spans all groups. No per-fact audience, only a single partition label per node/edge.
- Backends: Neo4j 5.26, FalkorDB 1.1.2, Amazon Neptune (+OpenSearch), Kuzu (deprecated, "upstream Kuzu project is no longer maintained"); Zep's own "Konig" graph DB for the managed service [P: README lines 105, 165-166, 215]. No Postgres driver in `graphiti_core/driver/` (directories: falkordb, kuzu, neo4j, neptune) [P]. Answer to "can it run on Postgres": no, not in this release.
- Zep Community Edition: "no longer supported. Its code has been moved to the legacy/ folder"; the `getzep/zep` repo is examples/integrations for Zep Cloud [P-ish: https://github.com/getzep/zep]. So Zep as a product is cloud only.
- Paper results [V: Zep authors]: DMR 94.8% (gpt-4-turbo) vs MemGPT 93.4%; 98.2% with gpt-4o-mini vs full-conversation 98.0% (Table 1). LongMemEval_s: gpt-4o-mini full-context 55.4% at 31.3 s, 115k context tokens vs Zep 63.8% at 3.20 s, 1.6k tokens; gpt-4o 60.2% vs 71.2% (Table 2). Single-session-assistant got worse (-9.06% mini, -17.7% 4o) and knowledge-update with mini dropped 3.36% (Table 3) [P]. The paper itself reports LLM calls with gpt-4o-mini for graph construction, i.e. several LLM calls per episode (extraction, resolution, fact extraction, dedupe, temporal extraction prompts listed in the appendix) [P outline]. Cost per message is therefore materially higher than one extraction call.

**Letta / MemGPT: BORROW IDEAS (core blocks); do not adopt**
- MemGPT paper: main context vs external context (recall storage + archival storage), queue manager, "memory pressure" warnings, function calls to page memory [P: text scan of 2310.08560; headings at lines 101, 164, 201-229]. No access-control or multi-party design in the paper that I found [U: scanned only for keywords].
- Letta docs: blocks have label + char limit; one block can be attached to many agents ("update once, visible everywhere"); `read_only` blocks cannot be changed by the agent; "last write wins" on `value`; docs describe no identity-based permission model beyond read-only [P-ish: docs page via summariser].
- The open `letta-ai/letta` repo is now a pointer: current source is `letta-ai/letta-code` (TypeScript CLI/harness with Slack/Telegram/Discord channels), V1 Python server is archived [P: README]. Do not build on the retired server. I did not evaluate letta-code.

**LangMem (langchain-ai/langmem, MIT, last commit 2026-10-02): BORROW IDEAS lightly; IGNORE as dependency**
- Memory is stored in LangGraph's `BaseStore` under namespace tuples like `("memories", "{langgraph_user_id}")`, templated at runtime from config [P: extraction.py ~699-727]. Scope = a path prefix chosen by the app; no audience semantics. Mem0 paper measured LangMem search p50 17.99 s / p95 59.82 s [V, competitor-run], which makes it unsuitable for interactive use per that benchmark.
- Useful ideas: memory manager that reconciles extracted items with existing items; prompt optimisation module (not relevant to our requirements).

**Cognee (topoteretes/cognee, Apache-2.0): BORROW IDEAS (provenance deletion); IGNORE as store**
- Has relational + vector + graph layers; vector adapters include pgvector; graph adapters: Kuzu, Neo4j, Neptune, a `postgres_demo` graph-as-tables adapter, which says verbatim "Using Postgres as a graph store is currently a demo feature and is not production-ready" [P: postgres_demo/__init__.py].
- Provenance: stable `source_ref` keys per (dataset, data item) and per chunk; deletion uses a "refcounted planner" where "shared output survives until its LAST owner goes" [P: provenance/source_refs.py docstring]. Same refcount idea as Graphiti's `remove_episode`, but with chunk-level keys.
- Access control is dataset-level permissions (file names under `modules/users/permissions`) [P names only; semantics U].

**Memobase (memodb-io/memobase, Apache-2.0, last commit Jan 2026): IGNORE**
- Postgres + Redis server; tables `User`, `UserProfile`, `UserEvent`, `UserEventGist`, `GeneralBlob`, `BufferZone`, `Project`, with pgvector columns [P: database.py]. Scoping is `project_id` + `user_id` (one profile per user). Profile-slot design (topic/subtopic) is a clean idea for the owner brief but we already have typed `owner_preference`/`contact` tables. Stale repo, single-user-centric. Claims of benchmarks not checked [U].

**MemOS (MemTensor/MemOS, Apache-2.0): IGNORE**
- MemCube abstraction with `readable_cube_ids`/`writable_cube_ids` in API examples; Docker setup "starts MemOS API + Neo4j + Qdrant"; Postgres not mentioned [P-ish README]. Claims LoCoMo 88.83, LongMemEval 89.20 [V, README, unverified]. Adds two databases; enforcement model undocumented.

**Supermemory (supermemoryai/supermemory, MIT per repo page): IGNORE**
- Scoping by `containerTag` strings; "Handles temporal changes, contradictions, and automatic forgetting"; local binary with embedded graph engine and local embeddings; claims "#1 on every major AI memory benchmark", "95% Recall@15" [V: self-reported, unverified]. Hosted core vs OSS boundary unclear from the page; no schema read [U].

**A-Mem (2502.12110): BORROW IDEA (low priority)**
- Zettelkasten-style notes with LLM-generated keywords/tags/context, embedding-based link generation to neighbours, and "memory evolution" where new notes rewrite old notes' attributes. LoCoMo Table 1 reports ~1,200 tokens per memory operation, 2,520 retrieval tokens (vs 16,900 for baselines), and < $0.0003 per operation [P: lines 326, 410-418; V]. Evolution that rewrites old notes is dangerous for provenance and erasure (derived text silently absorbs facts from other sources). Mem0's paper lists its own re-run of A-Mem at J 48.38 [V, competitor-run].

**HippoRAG (2405.14831): IGNORE for conversation memory; one idea**
- OpenIE triples to schemaless KG plus synonymy edges, Personalized PageRank from query entities; "10-20 times cheaper and 6-13 times faster than iterative retrieval like IRCoT" for multi-hop document QA [P: abstract lines 23-29, V]. Built for static corpora, not evolving conversation with permissions. Idea worth keeping: query-entity-seeded retrieval, which we can approximate with entity-linked memory rows and an extra SQL arm.

**Generative Agents (Park et al. 2023, 2304.03442): BORROW IDEAS**
- Memory stream; retrieval score combines relevance, recency, importance; recency is exponential decay with factor 0.995 over hours since last retrieval; reflections are periodically synthesised higher-level memories fed back into the stream [P: lines 132-139, 695-701]. Maps to Superaide's existing `importance.py` and `sleep.py`. Reflections must inherit the intersection of their source audiences (see section 3).

**Platform-native options**
- Anthropic memory tool `memory_20250818`: client-side only; "Claude requests file operations, and your application runs them"; `/memories` is "a prefix that your handler maps onto real storage, such as a per-user directory or keys in a database"; available on Claude 4 and later; six commands (view, create, str_replace, insert, delete, rename); the docs list sensitive info, file size, expiration, and path traversal as the application's responsibility; the API injects a protocol that says to always view the memory directory first [P: full doc]. It has no scoping, audit, or erasure model of its own; every audience decision remains yours. A file-tree interface also makes the model choose paths, which conflicts with "the model never decides who may see what" unless the handler maps each path to an audience-filtered view.
- OpenAI Agents SDK sessions: stores conversation items only; SQLAlchemy/Postgres backend exists; "the session ID does not authenticate a user or authorize access to that history"; no extracted long-term facts; isolation = your session-id naming plus your own authorization [P-ish docs]. Equivalent to our `Event` log; not a memory system.
- Slack MCP server / Real-time Search: user-token OAuth; search scopes `search:read.public/private/files/users`; "unlisted apps are prohibited from using MCP"; rate limits per tool (Tier 2/Tier 4 for some) [P-ish: https://docs.slack.dev/ai/slack-mcp-server/]. RTS doc says the API lets apps retrieve Slack data "without storing customer information on external servers" and "You must not store or copy any of the data retrieved from this API" (`scratchpad/p/rtsapi.txt` lines 8, 371) [P-ish]. Conclusion: Slack search can be a live lookup tool, never a source for memory rows or embeddings. It also searches as a user, so results reflect that user's permissions, not the audience of the current conversation.
- Claude Agent SDK memory: not researched [U].

### Inferences
- The pattern across Mem0, Graphiti, LangMem, Supermemory, MemOS: scope is a flat label (user, group, namespace, container, cube) that the caller must remember to pass. None models "a fact may be shown to exactly this set of people" nor fails closed when the filter is missing. Superaide's `memory_filter` (allowed_kind + `allowed_person_ids @> audience` + trust + validity + conflict status inside the same query) is stricter than anything surveyed.
- Mem0 changing from UPDATE/DELETE decisions to ADD-only accumulation is evidence that LLM-decided deletion is error-prone in production; it fits our own approach of reconciliation labels (`duplicate_of/refines/contradicts/new`) with deterministic supersession in code.

### Gaps
- No independent (non-vendor) replication of any LoCoMo / LongMemEval / DMR number. The two papers (Mem0, Zep) disagree on comparative claims and use different benchmarks and judges; treat all as [V].
- Letta, Cognee permissions, Memobase retrieval, Supermemory, MemOS internals not verified at code level.

---

## 2. Requirement scoring table

### Takeaway
No system satisfies requirements 1, 3 and 4 together. Graphiti is closest on 4 and 5; Mem0 is the only one with a ready pgvector store but with JSONB payload filters and no RLS. Scores: Y = meets, P = partly, N = no, ? = unverified.

### Cited Findings
Req 1 audience filter in-query + fail-closed; 2 speaker/channel/thread/workspace/external-tenant filters; 3 Postgres 17 + pgvector + RLS tenant_id/agent_id; 4 provenance + cascading erasure + tombstones; 5 temporal validity; 6 cost/spend-cap/offline fake model; 7 license + solo-owner ops; 8 Person with many ChannelIdentity.

| System | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Superaide today (per ADR + audience.py) | Y (SQL, constants for unknown audience; fail-closed for unknown) | P (context kind/thread on Event; channel not a recall filter yet per ADR 0008) | Y | P (source_ref, passport, erase.py exist; not read) | P (valid_to, supersession) | Y (BudgetGuard, FakeLLM) | Y | Y |
| Mem0 OSS | N (flat ids, JSONB filter optional) | P (arbitrary JSONB metadata) | P (pgvector yes; no RLS/tenant column; history in SQLite) | P (history rows; erasure of history unverified) | N in V3 (ADD-only); P in graph mode | P (pluggable LLM; hidden calls; no cap) | Y Apache-2.0, moderate | N (one user_id) |
| Graphiti | N (group_id single label, optional) | P (group_id; no channel/thread) | N (Neo4j/FalkorDB/Neptune; no Postgres) | Y-ish (episode refs, remove_episode) | Y (bi-temporal) | N (5+ LLM calls per episode, own clients) | P Apache-2.0; second DB | N |
| Zep Cloud | P | P | N (hosted) | ? | Y | N | N (hosted, data leaves) | ? |
| Letta | N (shared blocks, read-only flag) | N | ? (V1 server archived) | ? | N | ? | P | N |
| LangMem | N (namespace prefix) | N | P (via LangGraph PostgresStore, unread) | N | N | P | Y MIT | N |
| Cognee | P (dataset perms) | N | P (pgvector yes; graph on Postgres is "demo") | Y-ish (refcounted source refs) | ? | N (heavy pipeline) | P Apache-2.0, heavy | N |
| Memobase | N (project + user) | N | Y-ish (Postgres+pgvector, plus Redis) | ? | ? | ? | P stale | N |
| MemOS | ? | ? | N (Neo4j+Qdrant) | ? | ? | ? | P | ? |
| Supermemory | N (containerTag) | N | ? | ? | ? | ? | ? | N |
| Anthropic memory tool | N (you build it) | N | n/a | N | N | Y (it is your own call) | Y | n/a |
| OpenAI sessions | N | N | P (Postgres via SQLAlchemy) | N | N | n/a | Y | N |
| Slack RTS/MCP | P (user permissions) | P | n/a | N (may not store) | N | n/a | P | N |

Evidence for the cells is in section 1; cells with "?" are unverified.

### Inferences
- Requirement 8 (Person to many ChannelIdentity) is unmet by every surveyed system because they treat the id string as the identity; this is already a Superaide strength (ADR 0008 notes the same about Mem0 `user_id`, Zep and Rasa).

### Gaps
- Superaide scores are from `audience.py` and the ADR, not from running the code or reading recall/erase.

---

## 3. Mechanisms to borrow, and what would not work

### Takeaway
Borrow four mechanisms and implement them as Postgres columns/queries with deterministic code deciding scope: bi-temporal supersession on MemoryItem, episode-to-fact refcounted erasure, nearest-neighbour reconciliation before write, and audience-intersection inheritance for any derived item.

### Cited Findings
- Graphiti's invalidation rule (`resolve_edge_contradictions`) is about 20 lines of pure date logic once the LLM has named the contradicted candidates [P]. Port to Postgres: add `valid_at`, `invalid_at` (event time), `created_at`, `expired_at` (system time) to MemoryItem (Superaide already has `valid_to`); on a new fact flagged `contradicts` by the reconciler, set `old.invalid_at = new.valid_at`, `old.expired_at = coalesce(old.expired_at, now())`. Query "current truth" with `invalid_at IS NULL OR invalid_at > now()`; query "what did Ava believe on date D" with `created_at <= D AND (expired_at IS NULL OR expired_at > D)`.
- Graphiti's candidate restriction: dedupe only against edges between the same entity pair [P: paper 2.2.2]. Equivalent for us: reconcile a new memory only against rows with the same `subject_person_id` AND an overlapping audience, which both cuts LLM context and avoids leaking another audience's text into the reconciliation prompt.
- Graphiti `remove_episode` / Cognee refcounted `source_ref`: keep a join table `memory_origin(memory_id, episode_id/event_id)`; on tombstone of an event delete its origin rows, then delete MemoryItems with zero remaining origins, and null/regenerate embeddings and summaries that depended on them [P pattern from both; Superaide already has MemoryOrigin].
- Mem0's step: retrieve top-s similar existing memories, show them with the candidate to the LLM, take one of ADD/UPDATE/DELETE/NOOP [P: paper sec 2]. Replace with Superaide's reconciliation labels and let code apply them; keep NOOP and `linked_memory_ids`-style cross-links as an `refines` relation [P: V3 ADD-only prompt]. Mem0 itself dropped UPDATE/DELETE in V3.
- Mem0 history table: per-memory old/new/event audit trail [P]. For us, store only ids and event type (no text) so erasure of a memory does not leave a copy.
- Letta read-only blocks: map to a "pinned owner brief" built by code (`profile.owner_brief`) that the model cannot write [P docs].
- Park et al.: retrieval score = relevance + recency (0.995 hourly decay) + importance; reflections are generated summaries [P]. Derived reflections must be stored with `allowed_person_ids = intersection of source audiences` (and `min_trust = max`), or be rebuilt per audience; otherwise reflection becomes a laundering path. None of the surveyed systems handles this [inference].
- A-Mem's "memory evolution" (new notes rewrite old notes) should not be borrowed: rewriting an existing row with text derived from another audience's message breaks both scoping and erasure [inference from P method description].

What would NOT work for us:
- Graphiti as the memory store: no Postgres driver [P], single `group_id` partition and optional filter [P], entity summaries are LLM-written text spanning episodes (an entity summary mixing facts from several audiences cannot be audience-filtered, and community summaries add a second derived layer) [inference from paper 2.2.1/2.3 structure], 5+ LLM calls per episode (extraction, entity resolution, fact extraction, fact resolution, temporal extraction listed in the paper appendix) that bypass our BudgetGuard unless wrapped [P outline].
- Mem0 as the store: audience would have to be crammed into the JSONB payload and every caller would need to pass the right filter; `filters` empty returns unfiltered results [P: pgvector.py]; no RLS; V3 has no supersession; speaker attribution goes to one of `user_id`/`agent_id`, so multi-party (several humans in one Slack thread) cannot be modeled except by a user_id per human, which loses audience semantics [P docs].
- Letta/MemGPT: agent decides what to page in and out, so memory access is model-driven, contrary to the "code decides" rule [P paper design]; shared blocks have no per-viewer filtering [P docs].
- Anthropic memory tool: viable only as a thin façade. If we expose it, the handler must map `/memories/<audience-key>/...` to rows chosen by code from the already-computed Audience, never let the model name a person or audience in a path, and reject `delete`/`rename` on rows it does not own [recommendation derived from P doc security section].
- Slack RTS/MCP as a memory source: storing or embedding its output violates the stated rule "You must not store or copy any of the data retrieved from this API" [P-ish]; usable only as a live tool call whose result is excluded from `learn_from_turn`.
- Any graph-first system: entity resolution by embedding+LLM across people is exactly the identity step Superaide must do in code from immutable platform IDs, not by names; letting an LLM merge "Sam" nodes risks cross-person contamination.

### Inferences
- The thing worth copying from graphs is not the graph but the edge's four timestamps and the episode reference list; both are plain columns in Postgres.
- A small `subject_person_id`/`entity` column plus an entity-linked retrieval arm gives most of HippoRAG's/Graphiti's multi-hop benefit for personal-assistant data volumes without a graph engine (hypothesis; no benchmark run).

### Gaps
- No measurement of how often our reconciler would need entity resolution; no evaluation on a multi-party benchmark (LoCoMo is two-speaker; none of the surveyed benchmarks tests audience leakage).

---

## 4. Recommended plan

### Takeaway
Non-integration. Keep Postgres + pgvector + RLS and the SQL audience gate; add bi-temporal columns, refcounted erasure, nearest-neighbour reconciliation (labels applied by code), and audience-intersection rules for derived rows. Re-evaluate Graphiti only if multi-hop questions over entities become a measured failure and a Postgres backend appears.

### Cited Findings
Steps (ordered by value per effort; mechanisms sourced above):
1. Temporal columns on MemoryItem: `valid_at`, `invalid_at`, `created_at`, `expired_at` (Graphiti edge fields, `edges.py` ~271-285). Map existing `valid_to` to `invalid_at`. Supersession sets old.invalid_at to new.valid_at; never delete (Graphiti rule); the audience gate keeps filtering on `invalid_at IS NULL OR > now()`.
2. Erasure by origin refcount: `MemoryOrigin` rows per (memory, event); tombstoning an event (Slack message_deleted) deletes origins, then memories with no origins, then their embeddings; mark summaries/episodes derived from it stale and regenerate (pattern from `Graphiti.remove_episode` and Cognee source_refs). Add a test that deleting a source leaves zero text copies including any audit/history table.
3. Reconcile before write: nearest neighbours restricted to the same subject and an overlapping audience, passed to the small model with a fixed label set; code applies the label (Mem0 step, Zep same-entity-pair restriction). Run under BudgetGuard; FakeLLM scripts the labels in tests.
4. Derived rows (summaries, reflections, sleep-job outputs): `allowed_person_ids` = intersection of sources, `min_trust` = max of sources, origins list = all sources; erase when any origin goes.
5. Retrieval: keep hybrid FTS + vector + RRF with the gate in every arm; add optional filters for channel/thread/workspace as score boosts or filters (ADR 0008 already keeps same-channel as a 0.10 tiebreak). Optional later: Park-style recency/importance blend.
6. If an Anthropic memory-tool façade is wanted for Claude-native behaviour, implement the six commands over an audience-filtered view of MemoryItem rows (handler-enforced), as in the Anthropic doc's "map /memories onto a database" note.
7. Benchmark plan: write a leakage suite (existing `tests/redteam`) before importing any benchmark; LoCoMo/LongMemEval only for recall quality, treating published numbers as [V].

### Inferences
- Adopting Graphiti would add Neo4j or FalkorDB (second database, Cypher, separate backups and RLS-less scoping) and push Superaide's spend off the cap; the offline FakeLLM test strategy would need a Graphiti LLM-client shim.
- Mem0's strongest practical contribution to us is operational (its V3 move to single-pass extraction cut calls to one per add), supporting a one-extraction-call-per-message budget.

### Gaps
- Cost per message for our reconcile step is unmeasured; estimate needs a run on the real model with real token counts.
- Whether Graphiti or Zep will ship a Postgres driver is unknown; none in the 0.31.2 tree.
- Legal reading of Slack's storage restriction for Ava's own channel (as opposed to RTS results) belongs to the Slack mechanics note (`05_slack_telegram_mechanics.md`), not verified here.
