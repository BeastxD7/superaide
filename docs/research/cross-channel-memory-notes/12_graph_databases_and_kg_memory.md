# Graph databases and knowledge-graph memory for Superaide (alternative or complement to Postgres)

Research date 2026-10-23. Evidence tags: [P] primary source text read (paper text extracted from the arXiv PDF/HTML, or official repo/docs page); [V] vendor-reported or authors-of-the-system-reported number; [S] secondary source (aggregator, blog, search snippet); [N] not read this session (stated from general knowledge, must be verified before relying).

What I read this session:
- Zep/Graphiti paper arXiv 2501.13956: PDF text extraction, Tables 1-3, entity-resolution prompts, conclusion.
- Mem0 paper arXiv 2504.19413: PDF text extraction, Tables 1-2, section 4.5 token/construction analysis.
- HippoRAG 2 arXiv 2502.14802: PDF text extraction, Tables 2, 12 (and ablation text).
- LightRAG arXiv 2410.05779 (HTML, via fetch summary): Table 1 and sect. 4.5 cost analysis.
- Microsoft GraphRAG arXiv 2404.16130 (HTML, via fetch summary, first 100k of 110k chars).
- EverMemBench arXiv 2602.01313 (HTML v3, first 100k of 176k chars, Table 4 and findings).
- GroupMemBench arXiv 2605.14498: only the abstract and leaderboard table via the aggregator benchmarklist.com [S]. The paper body was NOT read.
- Repo/doc pages: kuzudb/kuzu, FalkorDB/FalkorDB, getzep/graphiti, apache/age, surrealdb/surrealdb GitHub pages; Neo4j licensing page, privileges docs, operations-manual intro, pricing page; Memgraph enterprise docs page.
- Search snippets only (no page read): ArangoDB license, TypeDB, Neptune pricing, Neo4j CE multi-db, AGE and RLS, graph-RAG privacy attack papers.
Not researched in any depth: Dgraph (no source found), Memgraph Community license text, Neptune IAM fine-grained access, TypeDB access control, Postgres recursive CTE/ltree/pgvector docs (no fetch; flagged [N]).

Background constants from the repo: Superaide is a single-tenant MVP today with tenant_id/agent_id on every row, audience filtering is done inside SQL, Postgres 17 + pgvector, a hard spend cap, offline FakeLLM tests (CLAUDE.md).

---

## 1. Does a graph help agent memory? What the evidence says

### Takeaway
On the benchmarks that exist, graph memory gives small, inconsistent gains over good flat/hybrid retrieval, mostly on temporal questions, and sometimes loses on multi-hop. In the only multi-party benchmarks (EverMemBench, GroupMemBench), graph-based systems (Zep, GraphRAG, HippoRAG, Mem0g-style) do not beat BM25 or full context. Nobody has shown a graph that beats flat tables plus hybrid search for audience-scoped multi-party memory, and the failure is upstream (attribution/ingestion), not the storage engine.

### Cited Findings
- Zep/Graphiti (authors' own paper) [V]: DMR 94.8% (gpt-4-turbo) vs full-conversation 94.4% and MemGPT 93.4%; with gpt-4o-mini Zep 98.2% vs full-conversation 98.0%. The authors themselves call the gain "marginal". LongMemEval_s: full-context gpt-4o-mini 55.4% / 31.3 s / 115k tokens vs Zep 63.8% / 3.20 s / 1.6k tokens; gpt-4o 60.2% vs Zep 71.2% (2.58 s). By type with gpt-4o: temporal-reasoning 45.1 -> 62.4, multi-session 44.3 -> 57.9, single-session-assistant 94.6 -> 80.4 (worse), knowledge-update 78.2 -> 83.3 [P]. For gpt-4o-mini, knowledge-update fell 76.9 -> 74.4 and single-session-assistant fell 81.8 -> 75.0 [P]. The baseline is full-context, not a tuned hybrid-retrieval flat store, so the paper does not isolate graph structure vs flat retrieval. https://arxiv.org/abs/2501.13956
- Zep paper itself says existing benchmarks "frequently default to simple needle-in-a-haystack fact-retrieval" and that none assess its structured-data case [P]. https://arxiv.org/abs/2501.13956
- Mem0 paper (Mem0 authors, competitor to Zep) [V]: LoCoMo LLM-judge J scores. Mem0 (flat, extracted facts) vs Mem0g (adds graph): single-hop 67.13 vs 65.71; multi-hop 51.15 vs 47.19; open-domain 72.93 vs 75.71; temporal 55.51 vs 58.13; overall J 66.88 vs 68.44. Zep: 61.70 / 41.35 / 76.60 / 49.31, overall 65.99. The paper itself says the graph "does not provide performance gains" on single-hop and multi-hop and gives gains on temporal and open-domain [P]. Net: graph adds about +1.6 overall J for roughly 2x memory tokens (Mem0 1,764 vs Mem0g 3,616 retrieved tokens; construction footprint 7k vs 14k tokens/conversation; Zep >600k tokens/conversation, vs raw 26k) and higher latency (search p95 0.200 s vs 0.657 s; total p95 1.440 s vs 2.590 s) [P]. The authors also report Zep graph construction was asynchronous and "retrieval attempts often failed" until hours later [V, about a rival]. https://arxiv.org/abs/2504.19413
- Mem0 paper full-context J 72.90 (26k tokens, p95 17.1 s) beat every memory system on J. Mem0 retrieval costs ~90% fewer tokens, so the argument for memory is cost/latency, not accuracy [P]. https://arxiv.org/abs/2504.19413
- HippoRAG 2 (OSU authors) [V]: Table 2 F1, Llama-3.3-70B reader. Avg: NV-Embed-v2 dense 57.0; HippoRAG 2 59.8; HippoRAG 1 53.1; GraphRAG 49.6; RAPTOR 48.8; LightRAG 6.6 (their reproduction, likely broken setup, treat as unreliable). Gains concentrated in multi-hop: 2Wiki 71.0 vs 61.5; MuSiQue 48.6 vs 45.7; HotpotQA 75.5 vs 75.3 (tie); NQ 63.3 vs 61.9; PopQA 56.2 vs 55.7. The paper says dense embeddings "surpass structure-augmented RAG methods with lower computational costs" [P]. Table 12 (MuSiQue 11,656 passages): HippoRAG 2 indexing 9.2M input + 3.0M output tokens, 99.5 min; GraphRAG 115.5M in + 36.1M out, 277 min; LightRAG 68.5M in + 18.3M out; dense NV-Embed-v2 needs no LLM tokens [P]. https://arxiv.org/abs/2502.14802
- LightRAG [V]: pairwise LLM-judge (GPT-4o-mini, 125 questions per dataset) win rates vs NaiveRAG 60-85%, but vs GraphRAG nearly 50/50 (Overall 52.0/47.2/49.6/54.8%). Cost analysis is the authors' estimate: GraphRAG retrieval uses about 610 communities x ~1,000 tokens = ~610k tokens, LightRAG <100 tokens and one call; both have "similar" extraction overhead [P]. Independent write-ups say the "6000x" gap applies to query phase only and indexing gap is much smaller [S]. https://arxiv.org/abs/2410.05779
- Microsoft GraphRAG [V]: designed for global sensemaking over ~1M-token corpora (podcast, news). Wins 72-83% comprehensiveness and 62-82% diversity vs vector RAG; vector RAG gave the most direct answers; evaluation is LLM-judge only, two corpora, fabrication not measured; entity matching is exact string match [P]. It is a summarisation technique over a static corpus, not conversational memory. https://arxiv.org/abs/2404.16130
- EverMemBench (multi-party, multi-group, 2,400 QA) [P, partial read], Table 4 accuracy: Gemini-3-Flash full context avg 72.61 (single-hop 97.65, multi-hop 26.51, temporal 45.00) vs +Zep 54.90 (68.54 / 6.02 / 11.00), +Mem0 52.12, +MemoBase 55.83, +MemOS 59.27. GPT-4.1-mini: full context avg 37.44, +Zep 39.97, +MemOS 42.55; multi-hop 2.41 full context vs 8.03 Zep vs 18.88 MemOS. With oracle evidence multi-hop jumps to 97.99% (GPT-4.1-mini) and 88.37% (Gemini), and accuracy drops with number of groups spanned (54.5% one group, 33.6% two, 19.7% three). Authors frame it as "an integration problem" not recall. https://arxiv.org/abs/2602.01313
- GroupMemBench (arXiv 2605.14498) [S, abstract + leaderboard via aggregator, self-reported, aggregator text contains an internal inconsistency on the Update number]: best system 46.0% avg; BM25 43.2 beats Mem0 25.7, GraphRAG 20.6, MemGPT 28.2, A-Mem 35.1, text-embedding-3-large 38.0 and ties/beats HippoRAG 39.7; GraphRAG multi-hop 12.09 vs BM25 40.11, temporal 5.56 vs 54.94. Authors' conclusion: ingestion "discards the structural and lexical features that group memory depends on". https://arxiv.org/abs/2605.14498 (read via https://benchmarklist.com/benchmarks/groupmembench_benchmarking_llm_agent_memory_in_multi_party_conversations/). Verify against the paper before quoting.
- Graph RAG privacy: 2026 papers show a black-box adversary can reconstruct a hidden entity-relation graph by adaptive querying, targeted one-hop subgraph reconstruction works even when verbatim disclosure is prohibited, and prompt-based safeguards give limited protection (arXiv 2602.06495 https://arxiv.org/html/2602.06495v1; GraphSteal arXiv 2605.28645 https://www.alphaxiv.org/abs/2605.28645.md; ACL 2026 long.727 https://aclanthology.org/2026.acl-long.727.pdf; PrivGemo arXiv 2601.08739). [S: read only search snippets, not the papers.] None addresses per-role access control on graph memory (my search found none).
- Failure modes documented in the Zep paper: entity resolution is an LLM call per extracted node against candidate existing nodes (prompt includes previous messages + existing nodes), edge dedup is the same pattern; hybrid search narrows candidates [P]. Graphiti README: "works best with LLM services that support Structured Output", smaller models may cause "incorrect output schemas and ingestion failures"; default SEMAPHORE_LIMIT 10 [P]. https://github.com/getzep/graphiti
- GraphRAG authors note exact-string entity matching [P]; no paper I read reports an entity-resolution accuracy number for conversational data (gap).

### Inferences
- Where graphs plausibly help: temporal change tracking (Zep +17 points temporal on LongMemEval, Mem0g +2.6 temporal J) and cross-document multi-hop on large corpora (HippoRAG 2 on 2Wiki). Both can be approximated in flat tables with valid_from/valid_to and a link table (my inference).
- Per-message cost: Zep-style ingestion makes several LLM calls per episode (extract entities, resolve each, extract edges, dedup edges, summaries) and Zep's store ballooned to >600k tokens per 26k-token conversation [P]. Under a spend cap on a small model this is the dominant cost, and it is paid whether or not the graph is stored in Neo4j or Postgres. The graph DB choice does not change extraction cost.
- Evidence is thin: the strongest "graph wins" numbers are from the vendors, with full-context or weak baselines, LLM judges, and single-user dialogues. The two multi-party benchmarks show the opposite. Treat "graph beats flat" as unproven for Superaide.

### Gaps
- GroupMemBench and EverMemBench bodies not fully read; Zep not on the GroupMemBench leaderboard. No head-to-head of Graphiti vs a tuned Postgres hybrid store (FTS + pgvector + temporal columns) exists in what I found.
- No numbers for entity-resolution error rates or duplicate-node rates in conversational graphs.
- Tokens per message for Graphiti ingestion not measured by me; only Zep 600k vs 26k footprint (Mem0's measurement of a competitor).

---

## 2. Database options

### Takeaway
Licensing and access control, not speed, are decisive. Neo4j fine-grained RBAC and multi-database are Enterprise/paid only; FalkorDB is SSPL; Kuzu is archived; SurrealDB is BSL and has had permission-bypass advisories; AGE is Apache-2.0 and keeps data in Postgres (so Postgres controls apply, but RLS on AGE was not confirmed by any source).

### Cited Findings
- Neo4j: Community Edition is GPL v3 (licensing page lists "Neo4j Community Edition (GPL v3)", free for "internal, non-production use" per that page's wording on developer offerings; Enterprise governed by Neo4j Software Agreement) — https://neo4j.com/licensing/ [P]. RBAC is Enterprise ("role-based access control and LDAP", Enterprise "includes everything the Community Edition offers") — https://neo4j.com/docs/operations-manual/current/introduction/ [P]. Community: system DB plus one user database, all users full access (forum/Cypher manual snippets) [S]; https://community.neo4j.com/t/can-we-have-multiple-database-on-community-edition/16265. Privileges can be granted/denied on NODES labels, RELATIONSHIPS types, ELEMENTS, with GRANT/DENY semantics ("access if you have a GRANT and do not have a DENY") and a property qualifier; edition not stated on that page but header lists Enterprise/Aura Business Critical/VDC — https://neo4j.com/docs/operations-manual/current/authentication-authorization/manage-privileges/ [P]. Aura: Free $0; Professional from $0.09/GB/hour (no RBAC, no multi-db); Business Critical from $0.20/GB/hour, 2 GB min (RBAC + multi-db included); VDC "talk to sales" — https://neo4j.com/pricing/ [P].
- FalkorDB: SSPL v1; uses GraphBLAS sparse-matrix adjacency; described as "multi-tenant" with separate named graphs; page gives no benchmark numbers and no ACL documentation — https://github.com/FalkorDB/FalkorDB [P]; docs say vector similarity + full-text + range indexes in one engine, no ACL/roles section — https://docs.falkordb.com/ [P]. Performance claims are vendor-reported, not verified [V].
- Kuzu: GitHub repo archived by owner on Oct 10, 2025 (read-only), MIT, 532 forks; "developing something new" — https://github.com/kuzudb/kuzu [P]. Graphiti marks Kuzu deprecated "will be removed in a future release" — https://github.com/getzep/graphiti [P]. Verdict: do not build on it.
- Graphiti: Apache-2.0; supports Neo4j 5.26, FalkorDB 1.1.2, Amazon Neptune Database Cluster; bi-temporal tracking with automatic fact invalidation; security warning "Any caller can read, change, or delete data in any group_id"; MCP server has "no authentication, no authorization" — https://github.com/getzep/graphiti [P]. No Postgres driver (from task brief).
- Memgraph: RBAC, label-based and property-based access control, multi-tenancy and HA are listed as Enterprise-only — https://memgraph.com/docs/database-management/enabling-memgraph-enterprise [P]. Community license text not read (gap).
- Apache AGE: Apache-2.0; supports Postgres 11-18; v1.8.0 badge; property indexes; openCypher; page does not mention RLS and lists transaction-visibility and `ag_catalog` ownership caveats — https://github.com/apache/age [P]. Search found no source on RLS with AGE; labels are stored as ordinary tables in a per-graph schema (DEV Community, https://dev.to/rafsun42/how-apache-age-turns-a-relational-dbms-into-a-graph-dbms-1i48) [S]. Whether Cypher queries respect RLS on those label tables is untested/unknown. Also an inference: Cypher MATCH over many-hop paths bypasses your own SQL ACL join, so you'd need RLS on every label table to fail closed.
- SurrealDB: core under BSL 1.1 (additional use grant/change date not read), SDKs Apache/MIT — https://github.com/surrealdb/surrealdb [P]. Has per-table, per-operation record permissions and field permissions; default-FULL-permissions advisory GHSA-x5fr-7hhj-34j3; 2026 advisories: WHERE evaluated before SELECT permissions (fixed 3.1.0) and array-element-level permissions not enforced (fixed ~3.1.4) — https://github.com/surrealdb/surrealdb/security/advisories/GHSA-x5fr-7hhj-34j3 [S, snippets]. Relevant: a new DB's in-DB ACL has a leakage track record. Graph + doc + vector in one engine is attractive but young.
- ArangoDB: source moved Apache-2.0 -> BUSL 1.1 from 3.12 (reverts after 4 years); Community binaries have a 100 GiB dataset cap and commercial-use restrictions per docs; from 3.12.5 Community includes Enterprise features — https://docs.arango.ai/arangodb/3.12/release-notes/version-3.12/incompatible-changes-in-3-12/ and https://arango.ai/?p=2755 [S: search snippets; license text not read].
- TypeDB: MPL-2.0 listed on Wikipedia; 3.0 Rust rewrite released Dec 2024; edition split unclear — https://en.wikipedia.org/wiki/TypeDB [S]. Dgraph: no source found. Neptune: Neptune Analytics billed m-NCU/hour (1 m-NCU = 1 GB memory), paused graph 10% — https://aws.amazon.com/neptune/pricing [S snippets]; Neptune Database serverless ~ $0.1098/NCU-hour with 1 NCU min, ~$80/month always-on (usage.ai, unverified secondary) [S]. AWS lock-in and minimum monthly cost make it a poor fit for a solo self-hosted owner (inference).
- Postgres ltree / recursive CTEs / pgvector: [N] not fetched. General knowledge: recursive CTEs handle bounded-depth traversal; ltree models hierarchies (trees), not general graphs; pgvector + FTS give hybrid in one SQL statement and compose with RLS. Verify at postgresql.org/docs before relying.

### Inferences
- For the "fail-closed on traversal" requirement, the engine with the least new trust surface is the one where the ACL predicate sits in the same query as the traversal over relational tables you already RLS/filter (Postgres). Enterprise-only Neo4j LBAC/PBAC gives denies but needs paid license for a hosted multi-tenant product; GPL3 Community is fine to self-host internally but lacks RBAC and multi-db, and licensing for a hosted commercial product built on GPL Neo4j was not analysed (flag for a lawyer).
- FalkorDB SSPL is a risk only if Superaide were offered as a managed graph service; for an app that merely uses it, SSPL is usually acceptable but needs legal review [inference, N].

### Gaps
- Dgraph, Memgraph Community license, TypeDB access-control, Neptune IAM fine-grained access, SurrealDB BSL change date, Neo4j property-level privilege edition: not verified.

---

## 3. Comparison table

| Option | License / cost | Tenant isolation | Fine-grained ACL on traversal | Bi-temporal / provenance | Solo-owner ops | Verdict |
|---|---|---|---|---|---|---|
| Postgres (current) + recursive CTE + FTS + pgvector | PostgreSQL license, free [N] | tenant_id + RLS, same as today | Predicate in same SQL, RLS possible [N] | Build with valid_from/to + source_message FK (inference) | Already running | ADOPT NOW (baseline, extend) |
| Postgres + Apache AGE | Apache-2.0 [P] | Postgres roles/RLS; RLS-on-AGE unverified | Unknown, must test per label table | Build yourself | Extension install; managed PG often lacks it (inference) | IGNORE for now; revisit if RLS-on-AGE proven and traversal queries become painful |
| Neo4j Community | GPL v3 [P] | one user DB only [S] | none (all users full access) [S] | Graphiti gives bi-temporal | JVM service | IGNORE as ACL boundary; only as candidate generator |
| Neo4j Enterprise / Aura Business Critical | commercial; Aura BC from $0.20/GB/hr [P] | multi-db [P] | label/type/property GRANT/DENY [P] | via Graphiti | Managed available | ADOPT LATER only if hosted product needs it and Postgres hybrid proven insufficient |
| FalkorDB | SSPL [P] | named graphs per tenant | no documented ACL [P] | via Graphiti | Redis-module, light | ADOPT LATER as optional Graphiti backend / candidate generator, not as ACL layer |
| Memgraph | Enterprise for RBAC/LBAC/multi-tenancy [P] | Enterprise | Enterprise | self-built | C++ in-memory | IGNORE |
| Neptune | AWS pay, always-on cost [S] | account/cluster | IAM (not read) | Graphiti supports | AWS lock-in | IGNORE for solo self-host; reconsider if hosted on AWS |
| ArangoDB | BUSL 1.1, 100 GiB cap on Community binary [S] | databases | RBAC | self-built | multi-model | IGNORE |
| SurrealDB | BSL 1.1 [P], young, ACL advisories [S] | namespaces | record/field permissions, bugs 2026 [S] | self-built | single binary | IGNORE for now |
| TypeDB | MPL-2.0 [S] | databases | not verified | strong typed schema | Rust rewrite 2024 | IGNORE |
| Dgraph | not researched | | | | | IGNORE (no evidence gathered) |
| Kuzu | MIT, archived Oct 2025 [P] | | | | | IGNORE (dead) |

---

## 4. Does a graph DB beat flat tables + hybrid search for this use case?

### Takeaway
No, not on the evidence, and not on requirements. The honest answer: unproven and probably not. The hard requirements (fail-closed audience, tenant isolation, cascading erasure, entity resolution of one person with many identities) are all easier when the system of record is relational with ACL and provenance in the same transactional store. The parts of a graph worth having (typed edges, temporal validity, multi-hop expansion) can be modelled as tables and bounded recursive CTEs.

### Cited Findings (where the argument comes from)
- Gains from graph memory are small/mixed and mostly temporal (sections 1 above).
- On multi-party benchmarks, graph systems lose to BM25 and to full context (EverMemBench Table 4, GroupMemBench leaderboard).
- Graph traversal enlarges privacy exposure surfaces (arXiv 2602.06495, GraphSteal) [S].
- Graphiti's group_id is a partition label, not authorisation (README security warning) [P].

### Inferences
- Failure modes specific to your requirements: (a) an LLM-extracted edge Person A -[works_with]-> Person B created from a private DM is itself a fact that may need DM-level audience; graph merges (entity resolution) can fuse facts from different audiences onto one node and leak by union. (b) Erasure: a node/edge supported by several episodes needs reference counting over provenance; Graphiti has episode provenance, but cascade delete semantics for "supported only by this message" are something you implement either way. (c) Staleness and duplicate nodes grow with LLM resolution errors; no measured rates available.
- In SQL the same cascade is `DELETE FROM fact_support WHERE message_id=...` then delete facts with zero support; on Postgres it is one transaction.

### Gaps
- No experiment of my own was run. A decisive test would be to build the reference design below as tables, run GroupMemBench/EverMemBench-style questions with and without the 1-2 hop expansion, and see if it moves multi-hop.

---

## 5. Reference design if a graph is used (recommended: graph as a view over Postgres, not a second store)

Principle: Postgres stays the system of record and the only authority for audience, tenant, erasure. Any graph engine (if ever added) is a derived, rebuildable index used only for candidate generation, followed by a mandatory Postgres re-check. This mirrors the repo's existing rule that the DB filters before the prompt (context/audience.py).

Node tables (all with tenant_id, agent_id; "node" = row):
- Person(person_id, display_summary, authority)
- Identity(identity_id, person_id FK nullable, channel, platform_user_id UNIQUE per channel, verified_at); ER is deterministic by platform ID (as today); LLM never merges Persons (inference from CLAUDE.md policy). Merge = admin/verified-link action, versioned.
- Room(room_id, channel, platform_room_id, kind dm|group|channel, audience_id) ; Thread(thread_id, room_id, platform_thread_id)
- Message(message_id, room_id, thread_id, speaker_identity_id, ts, text_ref) : source of truth, erasable
- Fact(fact_id, text, embedding, tsv, valid_from, valid_to, recorded_at, invalidated_at, audience_id, confidence)
- Topic(topic_id, name, embedding)
- Commitment(commitment_id, owner_person, counterparty_person, due, status, audience_id)

Edge tables:
- fact_support(fact_id, message_id) : provenance; the only way a Fact exists. Erasure: delete message -> delete its fact_support rows -> delete Facts with no support left (and recompute audience from remaining supports). Fail closed: Fact audience = intersection (most restrictive) over supporting messages' room audiences unless explicitly widened by a policy (inference/design choice).
- fact_about(fact_id, person_id | topic_id), fact_supersedes(new_fact_id, old_fact_id) for bi-temporal updates, commitment_from(commitment_id, message_id), message_in(thread/room), identity_of(identity_id, person_id).
- Rule: every edge row carries its own audience_id (denormalised, equal to the fact's) so that a traversal join needs no node lookup to deny; edges can never be more visible than both endpoints.

Audience enforcement options (ranked):
1. Recommended: ACL on every edge/row + traversal as a SQL recursive CTE with the audience predicate in each join step (the `WHERE audience_id = ANY(:allowed)` in the base and recursive terms), depth cap 2, row cap. Optionally RLS policies using a session setting `app.audiences` so even hand-written queries fail closed [N, RLS from Postgres docs not read this session].
2. Graph engine for candidate generation (Neo4j/FalkorDB via Graphiti) then Postgres re-check by fact_id: adds a second store and still needs rebuild/erasure sync. Only valuable if traversal depth/size outgrows CTEs. Never return graph text directly; return only IDs. Neighbour counts and existence are leaks too, so also drop denied candidates before ranking and do not log or summarise them.
3. Materialised per-audience subgraph (one graph per audience, FalkorDB named graphs or Neo4j multi-db): strongest isolation but fan-out cost on every write (fact written to N audience graphs) and erasure across N copies; practical only for few audiences (inference).

Extraction cost control: run extraction only on messages that pass a cheap triage (small model, once per message), store extractions per message with a content hash, cap candidate list in resolution to deterministic matches (platform ID, email, phone) so there is no per-node LLM resolution call; this removes the largest Graphiti cost items (per-node and per-edge LLM dedup calls) [inference from Zep prompts]. Offline testing: FakeLLM returns a scripted TurnOutput/extraction object; with SQL tables the whole pipeline remains testable on agent_test; Graphiti requires structured-output LLM + a graph server, harder to fake (README: needs structured output) [P/inference].

Verdict per option: Postgres hybrid (FTS + pgvector + temporal + support tables + bounded CTE) = adopt now. Graphiti on FalkorDB/Neo4j = adopt later only when (a) benchmarks on your own data show multi-hop/temporal gains beyond the Postgres variant, and (b) the audience re-check pattern 2 is implemented and red-team tested. AGE = revisit if RLS interaction is tested. All other engines = ignore.

### Gaps
- Not validated by running code. RLS-on-AGE and RLS on recursive CTEs not tested. Hosted multi-tenant licensing of GPL/SSPL/BSL components needs legal review.
