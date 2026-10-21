# Postgres remembers, code decides, Claude talks

Ava does not need a vector database. All of its knowledge can stay in the Postgres 17 instance it already runs. Add a full-text index and pgvector embeddings to the memory and document tables, and combine the two in one SQL query that runs inside Ava's existing audience filter. At one owner's scale, **10,000 facts stored as 1,024-dimension `halfvec` vectors take about 20 MB**, and an exact scan of one owner's rows needs no index. A dedicated vector store only starts to pay off at tens of millions of vectors. Ava's real weaknesses are elsewhere, and all of them show up in the current code:

- **Retrieval ignores the question.** Ava fetches the **newest 5 memories (30 for the owner)** whatever is asked.
- **Deduplication is naive.** Two facts count as duplicates only if their strings match exactly.
- **Long files are mostly unread.** Fact extraction sees only the first 12,000 characters of any file.
- **Removing a source only hides it.** Its facts are soft-retired, not deleted.
- **The never-share list is handed to the people it guards against.** The owner's free-text "never share" list is pasted into every prompt for a known person other than the owner. Benchmarks show models given private data plus warnings like this still leak it in **25–57% of cases**.

The fix follows one rule:

- **Postgres stores** each fact with its source, a trust rank and when it was valid.
- **Deterministic code decides** who may see what, which source wins a conflict, and whether to interrupt the owner.
- **Claude only extracts evidence and writes replies.**

In practice, that rule means five things:

1. Rank sources by trust: owner statement, then owner document, then system of record, then third-party claim, then web page.
2. When facts conflict, mark the old one as replaced instead of deleting it. Never let third-party or web content write the owner's preferences.
3. Have Claude Haiku 5.5 sort each inbound message into fixed labels with quoted evidence, at roughly **US$0.0003 per message**. Code then picks the outcome: handle it, take a message, add it to the digest, wait for a natural break, interrupt now, or check the sender's identity first.
4. Let strangers leave a message instead of hitting a dead end.
5. Send two or three digests a day, backed by an escalation ladder that waits for a break in the owner's day and only stops when the owner explicitly acknowledges.

The connection screens should stay paste-and-validate. Anthropic offers no third-party OAuth, and the Telegram and Slack flows Ava already ships are the recommended ones. WhatsApp should stay off for a Singapore owner under Meta's ban on general-purpose AI assistants. All six build increments fit inside the US$10 cap if development runs the chat model on Haiku and uses free or local embeddings.

## Ava already has the right three layers; newest-five retrieval is the weak joint

Every leading agent-memory system has settled on the same three layers:

- a small profile that is always in the prompt;
- extracted, consolidated facts, searched with combined keyword and vector search;
- raw episodes (the original messages), kept losslessly.

Letta reserves part of the context window for "core" blocks with a **2,000-character limit each** and pushes everything else into searchable archival and recall memory ([Letta docs](https://docs.letta.com/agents/memory)). Zep stores raw messages as "a non-lossy data store from which semantic entities and relations are extracted" ([Zep paper](https://arxiv.org/html/2501.13956)). Mem0 extracts facts from each new message pair, given the last 10 messages and the 10 most similar existing memories ([Mem0 paper](https://arxiv.org/html/2504.19413)). Ava matches this shape (code paths are under `backend/src/superaide/`):

- the audience-scoped owner brief in `knowledge/profile.py` is the core block;
- `memory_item` rows, with category, source, document link and confidence, are the facts;
- the `event` table is the episode log.

The architecture is not the gap.

Retrieval is. `visible_memories` in `context/audience.py` returns the most recent rows that pass the audience filter, with a limit of 5 for other people and 30 for the owner. One uploaded resume can produce up to 15 facts, and the intro call stores each question-and-answer twice, once raw and once as extracted facts. Together they quickly push older knowledge out of the top 30. Nothing guarantees that the facts relevant to the current question reach the model at all. A second blind spot sits upstream. Documents keep up to 60,000 characters of text (`knowledge/extract.py`), but `extract_facts` reads only the first 12,000 (`knowledge/facts.py`). Most of a long website or a multi-page CV is stored but can never be found.

### Don't choose a memory library from benchmark scores

Published memory scores do not support a choice between products. Depending on who ran the test, Zep's LoCoMo score ranges from **58.44 to 94.7**. Changing only the judge model moved one set of answers from 74.0 to 62.1, and prompt changes alone moved the same answers from 55.8% to 81.4%. Audits found **6.4–7.0% of the answer key** wrong or ambiguous, and the minimum detectable difference is about 4.5 points ([braindetox summary of CoreDotToday and Penfield](https://braindetox.kr/en/posts/memory_benchmark_scores_reliability_2026.html); [Penfield audit](https://dev.to/penfieldlabs/we-audited-locomo-64-of-the-answer-key-is-wrong-and-the-judge-accepts-up-to-63-of-intentionally-33lg)).

The baselines are more useful. Mem0's own full-context baseline scored about 72.9, above Mem0's 66.88 ([braindetox](https://braindetox.kr/en/posts/memory_benchmark_scores_reliability_2026.html)). Letta's plain file with `grep` on GPT-4o-mini scored **74.0% against Mem0's 68.5%** ([Letta](https://www.letta.com/blog/benchmarking-ai-agent-memory)). The pattern reverses at scale: on 115,000-token histories, Zep beat full context, **71.2% against 60.2%**, while sending 1.6k tokens instead of 115k ([Zep paper](https://arxiv.org/html/2501.13956)). The lesson is a curve rather than a winner. Small corpora do best when the model sees everything, and large ones need retrieval.

### What Ava should send to the model

For the owner in a private chat, include all of the owner's active facts while they fit in roughly 8,000 tokens (about 300 short facts), as a cached prompt block. A cache hit on Sonnet 5.5 costs **US$0.10 per million tokens**, so this stays well under a tenth of a cent per turn ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Above that threshold, switch to hybrid retrieval of the top 15 facts plus the core brief.

For third parties, never retrieve with their words as the query. In AirGapAgent, a "minimizer" that never sees the third party's messages chooses what the conversational agent may use. Under a single context-hijacking query it kept **97% of private data protected, against 45%** for the baseline ([AirGapAgent](https://arxiv.org/html/2405.05175v2)). Ava's shareable set is small and approved by the owner, so it can go in as a fixed card. Keeping the number of retrieved items small also matters for poisoning: in one replication, retrieving 10 memories instead of 3 raised memory-injection attack success **from 6% to 38%** ([arXiv 2601.05504](https://arxiv.org/html/2601.05504v1)). The code comment "few results limit memory poisoning" is right for low-trust audiences. For the owner, relevance should replace recency.

| Layer | Ava today | Change |
|---|---|---|
| Core brief | `owner_brief`, scoped by audience | Cap at about 2,000 characters; regenerate it in the background from high-importance facts; remove the never-share text from the version other people get |
| Typed settings | `contact`, `owner_preference` | Add triage fields (ladder, interrupt budget, deferral caps, autonomy grants) and per-contact disclosure grants |
| Atomic facts | `memory_item`; exact-string dedup; newest-N retrieval | Add a `tsvector`, an embedding, importance, a `time_bound` flag, trust rank, `superseded_by` and status; retrieve with hybrid search |
| Source text | `document.text` (up to 60k characters); `event` log | Chunk and index documents; move intro-call Q&A into a transcript document; keep the event log as the source of truth |
| Background work | None | Extraction, reconciliation, brief rewrite, archiving and erasure through a Postgres `SKIP LOCKED` queue |

## A separate vector database buys nothing at one owner's scale

Ava's Docker image already ships pgvector, so adding embeddings means adding a column, not a new system. The arithmetic follows from the per-type sizes in the pgvector README. A `halfvec` costs 2×d+8 bytes, so 10,000 facts at 1,024 dimensions come to about 20 MB per owner, and a `vector` column would be about 41 MB ([pgvector README](https://github.com/pgvector/pgvector)). Filtered by the existing B-tree on tenant and agent, an exact scan touches at most tens of thousands of rows, gives perfect recall, and avoids the known problem where an approximate index returns too few rows once filters are applied. Add an HNSW index only when total rows make exact scans slow, and turn on `hnsw.iterative_scan` at that point.

Two version details matter:

- **Pin the version.** pgvector **0.8.3 (17 June 2026) fixed possible HNSW index corruption during vacuuming**, and 0.8.4 added more vacuum fixes ([pgvector CHANGELOG](https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md)). Use 0.8.4 or later before relying on deletes for erasure.
- **Mind the dimension caps.** Indexes on `vector` stop at 2,000 dimensions and indexes on `halfvec` at 4,000, so 1,024 dimensions fit either way.

The best case for a dedicated store is a vendor benchmark that says it is "biased towards Postgres". At 50 million 768-dimension vectors and 99% recall, Postgres with pgvectorscale served **471.57 queries per second against Qdrant's 41.47**. Qdrant still had the better p95 latency (36.73 ms against 60.42 ms) and built its index in 3.3 hours instead of 11.1 ([Tiger Data](https://www.tigerdata.com/blog/pgvector-vs-qdrant)). Those trade-offs only bite at a scale Ava will not reach. Keeping one database is what lets one transaction cover facts, embeddings and deletes, and lets SQL enforce the audience gate before anything is ranked.

### Fuse keyword and vector search, and decay only facts that expire

A personal assistant hears proper nouns, phone numbers and dates, which keyword matching catches and embeddings often miss. That is why the pgvector README, Mem0's 2026 algorithm and Zep all add a keyword branch and merge it with vector results ([pgvector README](https://github.com/pgvector/pgvector); [Mem0](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm); [Zep paper](https://arxiv.org/html/2501.13956)). Postgres's built-in `tsvector` with `ts_rank_cd` is adequate at a single owner's row counts. The reported slowdowns, **25–30 seconds at 800,000 rows**, come from corpora far larger than one owner's ([Tiger Data pg_textsearch](https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres)). A BM25 extension such as ParadeDB's `pg_search` can wait until keyword ranking measurably fails ([ParadeDB](https://paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)).

Merge the two branches with reciprocal rank fusion (RRF) at k = 60, which is the published default. Then multiply the result by importance. Recency should also count, but only for time-bound facts. Generative Agents decays memories by 0.995 per hour since last retrieval ([Park et al.](https://arxiv.org/pdf/2304.03442)), and Mem0's decay never pushes a memory below 0.3× ([Mem0 decay](https://mem0.ai/blog/introducing-memory-decay-in-mem0)). That formula was designed for a simulation, though, and a CEO's role should not fade because nobody asked about it this month. Apply decay only to facts that extraction flags as `time_bound`, such as availability, plans and events, which is the distinction Mem0's 2026 memory types draw between "current" and "timeless" facts ([Mem0 temporal](https://mem0.ai/blog/the-token-efficient-memory-algorithm-now-has-temporal-reasoning)). The query wraps the existing gate rather than replacing it:

```sql
WITH q AS (SELECT $1::halfvec(1024) AS v, websearch_to_tsquery('simple', $2) AS t),
ok  AS (SELECT id FROM memory_item m
        WHERE {memory_filter(aud)} AND m.status = 'active'),   -- existing audience predicate, unchanged
vec AS (SELECT m.id, row_number() OVER (ORDER BY m.embedding <=> q.v) r
        FROM memory_item m JOIN ok USING (id), q ORDER BY m.embedding <=> q.v LIMIT 40),
fts AS (SELECT m.id, row_number() OVER (ORDER BY ts_rank_cd(m.tsv, q.t) DESC) r
        FROM memory_item m JOIN ok USING (id), q WHERE m.tsv @@ q.t LIMIT 40),
rrf AS (SELECT id, sum(1.0 / (60 + r)) s FROM (SELECT * FROM vec UNION ALL SELECT * FROM fts) u GROUP BY id)
SELECT m.*, rrf.s * (0.5 + m.importance / 10.0)
     * CASE WHEN m.time_bound
            THEN greatest(0.3, power(0.995, extract(epoch FROM now() - coalesce(m.last_accessed_at, m.created_at)) / 3600))
            ELSE 1 END AS score
FROM rrf JOIN memory_item m USING (id) ORDER BY score DESC LIMIT 15;
```

The `simple` text configuration is a deliberate choice. It skips English stemming, which tends to mangle names and code-mixed English and Hinglish, and the vector branch covers word forms anyway. The full-text branch needs no new dependency, so it should ship first. It fixes newest-N retrieval on its own, before any embedding exists.

### Embeddings: a local model avoids a fourth credential

Anthropic offers no embedding model. Its docs point to Voyage AI, whose voyage-4 API models default to 1,024 dimensions with a 32,000-token context ([Claude docs](https://platform.claude.com/docs/en/build-with-claude/embeddings)). voyage-4-lite costs **US$0.02 per million tokens, with 200 million free** ([Voyage pricing](https://docs.voyageai.com/docs/pricing)), so 10,000 facts (about one million tokens) cost about two cents to embed. Money is not the obstacle. The obstacle is that a non-technical owner would have to create a fourth account, a MongoDB Atlas key, on top of Claude, Telegram and Slack.

**voyage-4-nano** removes that step. It has open weights under Apache-2.0, about 0.3B parameters, and, according to Voyage, embeddings directly comparable with the API models "without re-indexing" ([HF model card](https://huggingface.co/voyageai/voyage-4-nano)). Ava can embed locally by default and offer the API later as a quality upgrade without re-indexing, though that compatibility claim is the vendor's own and should be spot-checked. Qwen3-Embedding-0.6B (Apache-2.0, 1,024 dimensions, 64.33 on multilingual MTEB) is the open fallback ([HF model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)). Either way, store `embedding_model` on every row so that a later re-embed is a background job. Test on code-mixed text before trusting any leaderboard.

Skip reranking at first. In Anthropic's Contextual Retrieval work, keyword search plus contextual embeddings cut top-20 retrieval failures from 5.7% to 2.9%, and a reranker only took that to 1.9%. Most of the gain came from first-stage retrieval ([beri.net summary](https://www.beri.net/article/cohere-vs-voyage-vs-bge-reranker-per-search-vs-per-token-cost-2026)). Voyage's hosted reranker adds about **287 ms at p95**, and that latency is the real cost in a chat.

The same index should cover documents. Following GraphRAG, where every extracted item references the text unit it came from ([GraphRAG](https://microsoft.github.io/graphrag/index/default_dataflow/)), split `document.text` into chunks in a `document_chunk` table that inherits the document's owner-only visibility. Each extracted fact then points to its chunk, and the owner can find details beyond the 12,000-character extraction window.

## Rank sources by trust, supersede instead of delete, and make erasure real

None of the memory systems reviewed publishes a scheme that trusts owner statements above third-party claims. Zep extracts the speaker as an entity, and Mem0 scopes memories by the `user_id` the application passes, but how far to trust each source is left to the application ([Zep paper](https://arxiv.org/html/2501.13956); [Mem0 group chat](https://docs.mem0.ai/platform/features/group-chat)). Ava therefore needs its own precedence order. The order below is a design inference, not a published standard. It separates the **author** of a statement from its **subject**: "Alice says the owner is moving to Bangalore" is Alice's claim, not an owner fact.

| Rank | Source | Example | May replace |
|---|---|---|---|
| 1 | Owner on a verified channel, including the intro chat, the intro call and edits in the notebook | "I've moved to Bangalore" in the owner's Telegram DM | Anything |
| 2 | Document the owner uploaded, newest version | Resume v2 | Ranks 2–5 |
| 3 | System of record, or a link the owner identified as their own | GitHub API profile; the owner's own site | Ranks 3–5 |
| 4 | Statement by a third party | A colleague says the board meeting moved | Nothing; stays a pending claim |
| 5 | Other public web content | A news page | Ranks 4–5 only |

### Supersede, never overwrite, and route conflicts to the owner

The leading systems have converged on marking old facts invalid rather than deleting them:

- **Zep** sets the old edge's `t_invalid` to the new edge's `t_valid`, and keeps both the valid-time and record-time timelines ([Zep paper](https://arxiv.org/html/2501.13956)).
- **Mem0's graph variant** marks conflicting relations invalid "rather than physically removing them" ([Mem0 paper](https://arxiv.org/html/2504.19413)).
- **Mem0 (2026)** switched to extraction that only ever adds facts, closes an evolving fact with `event_end` under a "state key", and keeps the history retrievable. After temporal reasoning was added, its own knowledge-update score fell **from 96.2 to 93.6** ([Mem0 temporal](https://mem0.ai/blog/the-token-efficient-memory-algorithm-now-has-temporal-reasoning)).

The vendor itself says update handling is not solved. For Ava, a newer fact replaces an older one only when its trust rank is equal or higher, so a 2026 Telegram statement replaces a 2023 resume line. A lower-trust claim becomes a pending item in the next digest instead: "Your resume says X; on Telegram you said Y on 12 Oct. Which is current?" The owner's answer is stored as a rank-1 fact. This costs little: Ava already has `valid_from` and `valid_to`, and it needs `superseded_by`, `trust_rank`, `author_person_id` and `conflict_status`.

"Newest wins" is exactly what memory poisoning exploits. The attacks and the defences tested against them:

- **MINJA** planted records just by sending ordinary queries, with **98.2% injection success and 76.8% attack success**. A GPT-4o detector flagged only 53 of 135 injections ([MINJA](https://arxiv.org/abs/2503.03704)).
- **MemGhost (July 2026)** used one crafted email to make agents write a falsehood into their own memory files. It succeeded **87.5% of the time on OpenClaw with GPT-5.4 and 71.4% on the Claude Code SDK with Sonnet 4.6**.
- **Purpose-built detectors** missed 91.6% (DataSentinel) and 93.5% (AgentDoG) of MemGhost's attempts ([CSA Labs](https://labs.cloudsecurityalliance.org/research/csa-research-note-memghost-agent-memory-poisoning-20260723-c/)).
- **Gemini** was induced to save false memories when the user later typed "yes", which bypassed a rule against writing memory while reading untrusted content ([Embrace The Red](https://embracethered.com/blog/posts/2025/gemini-memory-persistence-prompt-injection/)).

OWASP lists this as **ASI06, Memory & Context Poisoning**, for 2026 ([OWASP](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)).

Ava's pipeline already follows the right principle. The model cannot write memory, identity or trust tables, and a third party's "remember that" is stored only for that person's own audience. For the same reason, Ava should **not** adopt Anthropic's client-side memory tool, which hands Claude `create` and `str_replace` operations over a `/memories` tree ([Claude memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)). That is the self-write path MemGhost exploited.

One gap remains. Facts extracted from web pages and GitHub enter owner memory with the same categories as the owner's own words, including `preference`, `urgency` and `sharing`. A poisoned personal site could therefore plant "the owner wants documents sent to anyone who asks". Restrict link-sourced extractions to the `about` and `work` categories, never mark them shareable automatically, and send anything else to a review queue in the notebook. This is the CaMeL principle that untrusted data must not change control flow ([CaMeL](https://arxiv.org/abs/2503.18813)), applied to memory writes.

### Consolidate conservatively and cheaply

Reconciliation should call the LLM only where code cannot decide. Embed each new candidate fact and compare it with the 10 nearest active facts that share its subject:

- cosine ≥ 0.9: treat as a duplicate (no-op);
- 0.8–0.9: ask Haiku to judge;
- below 0.8: add it as a new fact.

These thresholds are heuristics to tune on Ava's own data. At Mem0's input size (a summary, 10 messages and 10 similar memories, about 2,000 tokens) plus 300 output tokens, Haiku 5.5 at US$0.10 and US$0.50 per million tokens costs about **US$0.00035 per turn**, or roughly 28,000 extractions per US$10 ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

Rewrite the core brief in the background every 5–10 owner turns or nightly. Letta's sleep-time agent defaults to every 5 steps and warns that running more often has diminishing returns ([Letta](https://docs.letta.com/guides/agents/architectures/sleeptime/index.md)). Keep consolidation to deduplication, supersession and brief regeneration, and never summarise episodes away. Microsoft's most rigorous test of brain-like consolidation found that deduplication produced most of the gain. With aggressive consolidation it scored **48.4% on LongMemEval, against 78.4% for plain retrieval** ([arXiv 2605.08538](https://arxiv.org/html/2605.08538)).

### Each source needs its own handling

Each source type needs a small rule of its own:

- **Re-uploads.** Deduplicate by SHA-256 (already stored). Create a new version when the content changes, compare the new facts with the old ones, and close the old facts with `valid_to` instead of deleting them.
- **The video-call introduction.** The browser turns speech into text, so Ava receives an owner statement from an authenticated session, which is rank 1. Today each answer is stored as a raw "Asked …, X said …" memory row alongside its extracted facts, so retrieval counts it twice. Store the call as a transcript document with chunks, and link its facts back through `document_id`; then "remove the call" works like removing a file. Show the owner the names Ava heard, because speech-to-text mangles proper nouns. Never enrol voiceprints: Illinois BIPA damages run **US$1,000–5,000 per violation**, and voice clones defeat them anyway ([DWT](https://dwt.com/blogs/privacy--security-law-blog/2024/08/illinois-bipa-biometrics-law-amended-for-damages)).
- **LinkedIn and X.** Ava already refuses both, which is correct. LinkedIn won a consent judgment in September 2026 forcing a scraper to stop and delete its data ([The Record](https://therecord.media/linkedin-wins-court-order-blocking-mass-scraping)), and X's terms have imposed **US$15,000** in liquidated damages for mass access since November 2024 ([Knight Institute](https://knightcolumbia.org/content/knight-institute-says-xs-new-terms-of-service-will-stifle-independent-research)). The owner's "Save to PDF" export is the right substitute.
- **GitHub and other links.** The GitHub REST API allows 5,000 requests an hour when authenticated ([GitHub Docs](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)). Ava's link fetcher already follows OWASP's "Case 2" guidance: it blocks private ranges, re-vets every redirect and pins the resolved IP address ([OWASP SSRF](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)).

### Removal should mean deletion

`retire_document` only sets `valid_to` on the facts and `removed_at` on the document. The document's text stays in the database. That is hiding, not erasure, and embeddings make the difference sharper. vec2text recovered **92% of 32-token inputs exactly** from their embeddings ([arXiv 2310.06816](https://arxiv.org/abs/2310.06816)). EDPB Opinion 28/2024 treats learned representations as anonymous only when extracting personal data from them is "insignificant" ([EDPB](https://www.edpb.europa.eu/our-work-tools/our-documents/opinion-board-art-64/opinion-282024-certain-data-protection-aspects_en)), and a practitioner reading of it concludes that derived summaries must be recomputed from surviving sources, not patched ([tianpan.co](https://tianpan.co/blog/2026-07-02-your-embeddings-are-pii)).

Keep the current behaviour as "hide", and add an "erase" path that runs in order:

1. Hard-delete the document text and its chunks.
2. Delete the facts whose only provenance is that document, and re-derive facts that have mixed provenance.
3. Regenerate the core brief.
4. VACUUM the affected tables.
5. Record the erasure in the audit log.

A small `derivation` table that records which source produced each derived row makes the cascade mechanical. Third parties need the same path, keyed by `person_id`. GDPR's one-month response clock and the PDPC's 30-day norm make a **30-day end-to-end erasure target** natural ([GDPR Art 12](https://gdpr-info.eu/art-12-gdpr/); [PDPC guide](https://www.pdpc.gov.sg/-/media/Files/PDPC/PDF-Files/Other-Guides/guide-to-handling-access-requests-v1-0-%28090616%29.pdf)). Retention should depend on who wrote the data. Raw content the owner authored can live as long as the account. Raw messages from third parties should roll off after 30–90 days, a design choice rather than a legal number, leaving only owner-approved summaries and a minimal contact card.

## Disclosure must be decided in SQL, because prompts leak a quarter of the time

The research is consistent that telling a model to be careful is not a control:

- **ConfAIde:** GPT-4 revealed private information in contexts where humans would not **39%** of the time, and ChatGPT 57% ([ConfAIde](https://arxiv.org/abs/2310.17884)).
- **PrivacyLens:** models answered about 99% of questions about privacy norms correctly, yet still leaked in **25.68–38.69%** of agent actions, even with privacy-enhancing prompts ([PrivacyLens](https://arxiv.org/pdf/2409.00138)).
- **PiSAs:** in a benchmark where one agent serves several users, strict explicit rules worked best, and a broad policy document did *worse than no rule at all* ([PiSAs](https://www.alphaxiv.org/abs/2607.05318.md)).
- **The "lethal trifecta":** Simon Willison's term for an agent that combines private data, untrusted content and the ability to communicate. Such an agent can be made to exfiltrate data, and prompt-level guardrails are "a failing grade" ([Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)).

Ava's core design already reflects this. `memory_filter` decides visibility inside the SQL query, before the model sees anything, and strangers get no model call at all.

The exception is serious and easy to fix. In the branch of `owner_brief` that serves anyone other than the owner, Ava appends "Never share, whatever the reason: {never_share}". Every prompt built for a linked colleague or VIP therefore contains the owner's own description of what is secret. "My home address", "that I'm interviewing with Grab" and "my daughter's school" all reach exactly the audience they were meant to be kept from. That turns the gate into the kind of broad prompt instruction PiSAs found counterproductive.

The fix has four parts:

1. **Keep the list on the owner's side.** The never-share text appears only in the owner's own prompt.
2. **Lock matching facts at write time.** A cheap Haiku pass, or a keyword match, labels facts that touch a never-share topic as owner-only with `min_trust = 4`.
3. **Check replies before they go out.** Scan every outgoing reply for never-share terms and owner-only canary strings, and hold any match for the owner.
4. **Tell everyone else less.** The non-owner prompt says only that everything else is private and offers to take a message.

### Tiers come from verified identity, not from what people claim

Delegation products already show workable defaults. Google Calendar's lowest sharing tier shows only free/busy "with no event names or details", and private events appear as bare busy blocks even to editors ([Google Calendar Help](https://support.google.com/calendar/answer/37082)). Outlook keeps "Delegates can see my private items" as a separate opt-in ([UVM IT](https://www.uvm.edu/it/kb/article/delegate-access/)). Anthropic's agent framework names the core failure: agents "might inappropriately carry sensitive information from one context to another" ([Anthropic](https://www.anthropic.com/news/our-framework-for-developing-safe-and-trustworthy-agents)). Mapped onto Ava's existing trust tiers and contact table, the defaults below follow from those precedents. The tiering itself is an inference.

| Audience, and how Ava knows | Shared by default | Needs a per-contact grant from the owner | Always needs the owner's real-time OK |
|---|---|---|---|
| Owner (linked, private DM or app session) | Everything | — | — |
| VIP or inner circle (linked identity, `contact.vip`) | Free/busy, "in a meeting until about 3pm", priority relay | City while travelling, meeting end times, assistant-held documents | Home address, live location, health, finance, other people's messages |
| Known contact (linked identity) | Free/busy, company and role, work email, if listed in `shareable` | Per-category grants | The same as above |
| Stranger, unverified account, group with unknown members, public web-chat visitor | That Ava is the owner's AI assistant; an offer to take a message | — | Everything else |

A claimed relationship never raises a tier: "I'm his wife" is a feature of the message, not a fact about the sender. Requests for owner-only categories go to the owner on a trusted channel whatever urgency the requester claims. Emergency framing is the signature of context hijacking ([AirGapAgent](https://arxiv.org/html/2405.05175v2)). For a claimed emergency, Ava offers to relay the message at once instead of disclosing anything. The owner should also hear about denied sensitive requests, because a pattern of them is an early warning of social engineering.

Implementing this needs one new table. Today `owner_preference.shareable` is a global list of five labels. A `disclosure_grant` table keyed by contact person, category, level (none, existence, summary or detail) and expiry gives the per-person control that Outlook's per-folder delegation models.

### Defence in depth, and an honest first message

Three further layers back up the SQL gate. The first is Postgres row-level security. Policies on `memory_item` and `document_chunk` would read the audience from a session variable, use `FORCE ROW LEVEL SECURITY` and `AS RESTRICTIVE`, and run under an application role that neither owns the tables nor holds `BYPASSRLS` ([PostgreSQL docs](https://www.postgresql.org/docs/current/ddl-rowsecurity.html); [Supabase](https://supabase.com/docs/guides/ai/rag-with-permissions)). A bug in a Python filter then cannot widen access. The second is to fix the plan before untrusted text is read. Under the "Plan-Then-Execute" pattern, a third party's message can change the content of a reply but not its recipients or actions ([Beurer-Kellner et al.](https://arxiv.org/pdf/2506.08837)). The third is to strip links and images from untrusted content in every rendering the owner sees, since EchoLeak exfiltrated data from Microsoft 365 Copilot through a reference-style Markdown image ([EchoLeak](https://arxiv.org/html/2509.10540v1)). Seed canary memories at every tier in `tests/redteam/` and assert that they never appear in prompts or replies at lower tiers. The fake model keeps those tests free.

Disclosure that Ava is an AI is now law in Europe and good practice everywhere. EU AI Act Article 50 has applied **since 2 August 2026**. People must be told they are interacting with an AI "at the latest at the time of the first interaction", and breaches carry fines up to **€15 million or 3%** of turnover ([AI Act Art 50](https://artificialintelligenceact.eu/article/50/); [Goodwin](https://www.goodwinlaw.com/en/insights/publications/2026/08/alerts-technology-dpc-eu-ai-act-transparency-obligations-now-in-force)). Ava's stranger reply already says "an AI assistant". The first message to each new counterpart should also name the owner, say the owner may review the conversation, and link a privacy notice. That link meets GDPR Article 14's duty to inform people whose data was obtained from someone else, at the first communication ([GDPR Art 14](https://gdpr-info.eu/art-14-gdpr/)).

The owner's personal-use exemption does not protect Superaide. PDPA section 4(1)(a) exempts individuals "acting in a personal or domestic capacity", not the company that processes the data ([PDPA s4](https://lawplayer.com/sg/act/PDPA2012/4)). GDPR Recital 18 still applies to those who "provide the means" for household processing ([Recital 18](https://gdpr-info.eu/recitals/no-18/)). Outsiders need consent, or deemed consent by notification after the section 15A assessment ([PDPA s15A](https://lawplayer.com/sg/act/PDPA2012/15A)). None of this is legal advice, and counsel should confirm the controller-versus-intermediary analysis.

## Haiku gathers evidence, code decides, and two or three digests replace interruptions

Interruptions cost far more than their own length:

- **Iqbal and Horvitz** found users spent about 10 minutes on diversions triggered by alerts and another **10–15 minutes** before returning to focused work, and **27% of suspended tasks took more than two hours** to resume ([CHI 2007](https://www.erichorvitz.com/CHI_2007_Iqbal_Horvitz.pdf)).
- **Gloria Mark** puts the return to an interrupted project at about **25.5 minutes** ([UCI](https://news.uci.edu/2023/05/05/uci-podcast-if-you-cant-pay-attention-youre-not-alone)).
- **Interrupted people worked faster but reported significantly more stress and frustration** ([Mark et al. 2008](https://www.interruptions.net/literature/Mark-CHI08.pdf)).

Silence has costs too. Twenty-four hours with notifications off made people feel more productive but more worried about missing something urgent ([Pielot & Rello](https://arxiv.org/pdf/1612.02314)). Batching notifications **three times a day** made people happier, less stressed and more attentive. Hourly batching gave few benefits, and no alerts at all *raised* anxiety ([Fitz et al.](https://communities.springernature.com/posts/batching-notifications-can-improve-attention-well-being-and-productivity)). The goal is trustworthy breakthrough plus a few digests, not maximum suppression.

Two mechanisms let Ava hold interruptions without losing them. Bounded deferral holds an alert until the user is free, but never past a preset cap. A four-minute cap cut busy-time alerts **from 20 to 4 for one participant and from 80 to 0** for another ([Horvitz et al.](https://www.interruptions.net/literature/Horvitz-UM05b.pdf)). Deferring to natural breakpoints held notifications for 88.6 seconds on average and lowered frustration ([Iqbal & Bailey](https://www.interruptions.net/literature/Iqbal-CHI08.pdf)). For a CEO in back-to-back meetings, the end of a meeting is the natural breakpoint. It is far more reliable than predicting interruptibility from sensors, which reaches 60–87% accuracy ([Mehrotra & Musolesi](https://arxiv.org/pdf/1711.10171)).

### The model supplies evidence; code makes the call

LLMs are good but not reliable judges of urgency. GPT-4 triaged 93% of simulated clinical messages correctly ([PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11417033)), but the best fine-tuned model ranked real hospital messages correctly in only **0.77** of pairs ([PMR-Bench](https://arxiv.org/html/2601.13178v1)). Models' stated confidence is badly overconfident, mostly 80–100%. Averaged across datasets, GPT-4's calibration error was 18.0 and its ability to separate right from wrong answers (AUROC) only 62.7. Measuring agreement across five samples was a far better signal, raising AUROC on GSM8K from 54.8 to 92.7 ([Xiong et al.](https://arxiv.org/html/2306.13063v2)).

Shipped products add three rules:

- **Explicit rules win.** Apple's rule: "Notifications you explicitly allow or silence are always handled that way" ([Apple](https://support.apple.com/guide/mac-help/mchldf5e4cb6)).
- **Show the reason.** Outlook shows a few lines of reasoning for each priority decision ([Microsoft](https://support.microsoft.com/en-US/Outlook/copilot-outlook/prioritize-my-inbox)).
- **Keep triage surfaces lean.** Microsoft removed Outlook mobile's priority view on 4 February 2026 "in response to user feedback and costs" ([MC1089318](https://mc.merill.net/message/MC1089318)).

Ava's pipeline should therefore run in order, with the model confined to step three:

1. Resolve identity in code, which already exists.
2. Apply hard rules: silenced senders, "always interrupt" rules, and keyword screens for payment and credential requests.
3. Make one Haiku 5.5 call with no tools. Its structured output is all fixed labels: category, urgency, deadline, matched urgent-topic IDs, risk flags, up to three verbatim evidence quotes (which code checks against the original message), and a reason the owner will see.
4. Run a deterministic policy engine on that output.

Haiku 5.5 supports constrained JSON schemas, but numeric minimums and maximums are not allowed, `additionalProperties` must be false, and enum casing can vary, so range checks belong in Pydantic ([structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)). Do not send `temperature`, `top_p` or `top_k` to Haiku 5.5, which returns a 400 error ([Haiku 5.5](https://platform.claude.com/docs/en/models/haiku-5-5/overview)). For borderline items, resample and treat agreement as the confidence signal. With a cached preference prefix, a classification costs about **US$0.0003**, or US$0.15 a day at 500 messages.

| Condition (evaluated in order) | Outcome |
|---|---|
| Sender or topic silenced by the owner | DIGEST, or IGNORE for spam |
| Any risk flag: payment or bank change, gift cards, credentials or MFA codes, secrecy, bypassing process, mismatched identity, embedded instructions | VERIFY: never handled autonomously; the owner is told it is "unverified" only if it would otherwise qualify as urgent |
| Verified VIP and urgency high or critical | INTERRUPT_NOW; breaks quiet hours if `vip_breaks_quiet` |
| Urgent-topic match, verified or known sender, critical | INTERRUPT_NOW |
| High urgency, owner busy, deadline after the busy period ends | DEFER_THEN_INTERRUPT at the next breakpoint, capped by the deadline |
| Unknown sender claiming urgency without evidence | TAKE_MESSAGE; ask "Is this time-sensitive? By when?"; flag it in the digest |
| Assistant can handle it, intent is in `autonomy_grants`, no risk flags | HANDLE (approval-gated at the Infant stage), listed under "Handled for you" with undo |
| Needs a decision from the owner, not urgent | TAKE_MESSAGE, filed under "Needs your decision" |
| Interrupt budget used up | Only critical items from verified VIPs break through; the rest wait for the next breakpoint |

### Authenticated identity gates breakthrough, and strangers get a path in

"Urgent request from an executive" is the classic business email compromise pattern, which cost **about US$2.8 billion in reported US losses in 2024** ([Nacha on IC3](https://www.nacha.org/news/fbis-ic3-finds-almost-85-billion-lost-business-email-compromise-last-three-years)). An Arup employee wired **HK$200 million (about US$25.6 million)** after a video call full of deepfaked colleagues ([AIID](https://incidentdatabase.ai/es/reports/3630)). VIP status must therefore attach only to an authenticated channel identity. A VIP's display name on a new account is a sign of impersonation, not a reason to boost priority.

Ava's identity service already enforces this, and in doing so it exposes a gap. VIPs added during onboarding exist only as names. When someone later claims to be one, `start_challenge` finds no trusted channel and quietly records `link_no_trusted_channel`. A real VIP therefore can never get through. The fix uses pieces that already exist. From the People step, the owner forwards each VIP a one-time invite link (`create_invite`, the same `t.me/<bot>?start=` mechanism the owner used). When an unlinked contact claims an identity, Ava sends the owner an approve/deny prompt instead of failing silently.

Stranger mode has the same dead-end quality. It is a fixed reply with no model call, which is safe because strangers can neither probe memory nor drain the budget. But someone with a genuine deadline has no way in. Change it to take-a-message:

- Strangers' messages go to the triage classifier only. It has no tools, no memory and an output limited to fixed labels: AirGapAgent's quarantined model, in effect.
- Ava asks what Pixel's Call Screen asks: "Is it urgent?" ([Pixel Call Screen](https://support.google.com/phoneapp/answer/9118387)).
- The message lands in the digest, or escalates only if it matches an owner-declared urgent topic and passes the risk checks.
- Per-sender caps (for example, three classified messages a day) and a daily stranger budget keep the US$10 cap from becoming a denial-of-service target.

### Escalate on a ladder, and count only explicit acknowledgements

PagerDuty supplies the escalation mechanics. Escalation policies default to a 30-minute timeout and repeat up to nine times. Acknowledging stops escalation without resolving the incident ([PagerDuty](https://docs.pagerduty.com/incident-management/schedules/escalation-policies)). Notification rules stagger channels: push at 0 minutes, phone at 1, Slack at 2, email at 3 and SMS at 4 ([PagerDuty rules](https://docs.pagerduty.com/account-admin/user-profile/notification-rules)). Opsgenie's documented pitfall, where simply viewing an alert stops further notifications, shows why "seen" must never count as "acknowledged" ([Atlassian Community](https://community.atlassian.com/forums/Opsgenie-questions/Why-does-opsgenie-skip-rule-if-user-saw-alert/qaq-p/2512917)).

For one owner sitting in a meeting, the timeouts should be minutes, not PagerDuty's thirty:

- **Step 0.** A message on `interrupt_channel` gives a one-line reason, the original quote and buttons: Ack, Call them, Reply "…", Not urgent. Slack needs interactivity enabled in the app manifest for the buttons.
- **Step 1, at 3–5 minutes.** If there is no acknowledgement, send on a second channel.
- **Later, critical only.** A phone call, or the owner's human delegate.
- **Cap.** One or two full cycles.

Repeat-sender breakthrough copies the phone rules: iOS lets anyone through who calls twice within three minutes, and Android does the same within 15 minutes ([Apple](https://support.apple.com/en-gw/guide/mac-help/mchl613dc43f/12.0); [Android](https://support.google.com/android/answer/9069335?hl=en)). Ava should apply it only to verified identities, because unverified repetition is a cheap lever for social engineering.

What Ava tells the requester must be honest and must not leak:

- "I've flagged this to her as urgent; she's in meetings until about 3pm" is fine. Naming the meeting is not.
- "She's seen it" is said only after an acknowledgement.

### Digests and owner settings

`owner_preference` already holds urgent topics, VIPs (through `contact`), quiet hours, `vip_breaks_quiet`, `interrupt_channel` and a single `digest` choice. Four changes complete it:

- **Add triage settings.** An ordered ladder of channels with delays, an interrupt budget per meeting and per day, deferral caps by urgency, `autonomy_grants`, lists of silenced senders and topics, and an optional human delegate.
- **Change the digest default.** Default to morning, midday and evening, add a catch-up when quiet hours end, and warn the owner who picks "none".
- **Require one urgent rule at setup.** Copy Outlook, which requires at least one high-priority instruction ([Microsoft](https://support.microsoft.com/en-US/Outlook/copilot-outlook/prioritize-my-inbox)).
- **Model "busy" without a calendar.** Ava has no calendar connection yet, so use quiet hours plus an owner command such as "/busy 90m".

Follow-ups and "waiting on others" items belong in a typed intention store managed by code. On a prospective-memory benchmark, such a store lifted a small model from 4.2% to 66.2% ([PIS](https://arxiv.org/abs/2609.01272)).

Each digest should be ordered by how much action it needs:

1. Needs your decision.
2. Escalations and whether each was acknowledged.
3. Handled for you, with undo.
4. Messages taken, with *exact quotes* wherever names, money, legal or HR matters appear. Apple paused its news summaries after one invented a death ([The Decoder](https://the-decoder.com/apple-pauses-ai-generated-news-summaries-after-serious-accuracy-issues-surface/)).
5. Pending conflicts in memory.
6. FYI counts.
7. A "low-confidence or suppressed" safety valve.

Every item carries "should have interrupted" and "not urgent" buttons. Gmail's Priority Inbox had to become "much more responsive to your manual corrections" ([Gmail blog](https://gmail.googleblog.com/2010/12/priority-inbox-update-time-savings-new.html)), and those labels become the owner's own evaluation set. Track five metrics: recall on interrupt-worthy items, interrupt precision, interrupts per day, median time to acknowledgement, and the rate of urgent items found buried in a digest.

## Paste-and-validate is the right connection UX, and WhatsApp stays off in Singapore

No single "Connect" button exists for these services, and for Anthropic it is forbidden. Anthropic's policy says third-party developers may not "offer Claude.ai login into their own applications, or … route requests through Free, Pro, or Max plan credentials", and may not "collect, store, or intermediate Claude.ai credentials or session tokens" ([Claude Code legal](https://code.claude.com/docs/en/legal-and-compliance)). Press coverage dates the ban to February 2026 ([Gigazine](https://gigazine.net/gsc_news/en/20260220-anthropic-third-party-block)). The only route is an API key the owner creates. Ava's flow already matches the guidance: a dedicated workspace with a monthly spend limit (the Default Workspace cannot have one), validation through `GET /v1/models`, AES-256-GCM encryption with the key slot as associated data, and a master key in the macOS Keychain ([Workspaces](https://platform.claude.com/docs/en/manage-claude/workspaces); [List Models](https://platform.claude.com/docs/en/api/models/list)). Two things are missing:

- **A rotation reminder.** Anthropic recommends rotating keys "for example, every 90 days" ([API key best practices](https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure)).
- **A kill-switch instruction.** Archiving a workspace revokes all its keys "within seconds" ([Workspaces](https://platform.claude.com/docs/en/manage-claude/workspaces)). Ava should tell the owner this as a second line of defence beyond its in-app spend cap and pause switch.

| Service | Recommended flow | Ava today | Still to add |
|---|---|---|---|
| Anthropic | Console key in a dedicated workspace with a spend limit; never an Admin key | Done | 90-day rotation reminder; "archive workspace" kill-switch guidance |
| Telegram | BotFather token, `getMe` check, long polling, `/start` code pairing | Done | Optional Business connection with minimal rights; Managed Bots only for a future hosted version |
| Slack | Pre-filled manifest, Socket Mode, `xoxb-` plus `xapp-` tokens | Done | Lock to the installing user; interactive ack buttons; Marketplace review before commercial distribution |
| WhatsApp | None for a Singapore owner | Marked unavailable | Revisit only for EEA owners, with counsel |

### Telegram

For a Mac behind a home router, long polling is the right default. Webhooks need public HTTPS on ports 443, 80, 88 or 8443, and Telegram keeps undelivered updates for only 24 hours ([Bot API](https://core.telegram.org/bots/api)). Telegram's **Managed Bots** (Bot API 9.6, 3 April 2026) make bot creation nearly one tap through a `t.me/newbot/...` deep link. The catch is that the new bot's token goes to an always-on "manager bot" ([changelog](https://core.telegram.org/bots/api-changelog); [Managed bots](https://core.telegram.org/api/bots/managed-bots)). In Hermes Agent's design, the developer-hosted manager "holds and relays every user's token" ([Hermes issue](https://github.com/NousResearch/hermes-agent/issues/10591)). That only fits a hosted Superaide, and the token should be rotated straight after hand-off.

Telegram Business is the more important option for Ava's purpose of fielding people who message the owner. Since Bot API 10.0 (8 May 2026), business bots can manage accounts without Telegram Premium ([changelog](https://core.telegram.org/bots/api-changelog)). A connected bot replies inside the owner's personal chats, but only in chats that "received incoming messages in the last 24 hours". Its powers are granular rights such as `can_reply` and `can_read_messages` ([aiogram](https://docs.aiogram.dev/en/latest/api/types/business_bot_rights.html)). The owner can pause it per chat, and filter by contacts or non-contacts ([connected business bots](https://core.telegram.org/api/bots/connected-business-bots)). Offer it as an opt-in second phase that requests only the reply and read rights. Treat every `business_message` from a non-owner as third-party content, and honour `deleted_business_messages` by deleting the raw message and any facts derived only from it.

### Slack

The manifest link plus Socket Mode is the right pattern. Slack's alternative, a localhost OAuth redirect with PKCE, "cannot request bot scopes" and makes the app a public client permanently ([Slack PKCE](https://docs.slack.dev/authentication/using-pkce)). The commercial caveat matters for a marketplace. Since 29 May 2025, commercially distributed apps outside the Slack Marketplace are limited to **one `conversations.history` request a minute, returning at most 15 objects**. Slack's terms also cover "instructions for a templated custom app" ([Slack changelog](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps)). Socket Mode apps cannot be listed in the Marketplace ([Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)). Ava logs messages from events it receives instead of reading history, so the throttle barely touches it today; this is an inference from the scope of the limit. A hosted, sold version would still need Marketplace review and HTTP events.

### WhatsApp

Meta's Business Solution Terms, last modified 6 March 2026, bar "AI Providers" from using the platform to make general-purpose assistants available. They make exceptions only for users with EEA or Brazil phone numbers ([WhatsApp terms](https://www.whatsapp.com/legal/business-solution-terms/)). The ban took effect for existing providers on 15 January 2026 ([TechCrunch](https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform)). The European Commission then ordered interim measures on 9 June 2026, requiring Meta to restore free access ([EC IP/26/1276](https://ec.europa.eu/commission/presscorner/api/files/document/print/en/ip_26_1276/IP_26_1276_EN.pdf)), and Meta appealed in August ([Slaughter and May](https://www.slaughterandmay.com/recent-work/meta-on-its-appeal-against-the-european-commission-s-whatsapp-interim-measures-decision/)). A Singapore owner falls outside every exception.

The mechanics are poor as well. Embedded Signup requires an approved Tech Provider with business verification and App Review ([Meta](https://developers.facebook.com/docs/whatsapp/embedded-signup/)). The do-it-yourself route needs a separate number, a System User token and a public HTTPS webhook ([Cloud API](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started)). Since 1 October 2026, service messages beyond **1,000 free per number per month** are charged ([Meta pricing](https://developers.facebook.com/docs/whatsapp/pricing)). Unofficial WhatsApp Web bridges breach WhatsApp's terms of service ([WhatsApp ToS](https://www.whatsapp.com/legal/terms-of-service)). Keeping WhatsApp "unavailable" is correct. Regulators, not engineering, will decide when that changes, so the channel interface should stay pluggable.

### Secret handling

One gap remains in secret handling. A search of the codebase found no logging filter that redacts secrets. Add one for `sk-ant-`, `xox[bpa]-` and Telegram's `digits:35-character` token shape, and never log request bodies on connection routes. OWASP says secrets must "never be logged" ([OWASP Secrets](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)). Also record each decryption in the audit log, without the value. The Keychain protects against database dumps and leaked backups, not against malware running as the user: on macOS, any script run by the same Python interpreter can read keyring items without a prompt ([keyring](https://pypi.org/project/keyring/)).

## Six increments fit inside the US$10 cap

Order the work by risk. The disclosure leak is the only live defect, so it goes first. Relevance retrieval next unblocks everything that depends on Ava finding the right fact. Consolidation and triage can then build on reliable retrieval.

| # | Build | Done when | Model spend |
|---|---|---|---|
| 1 | Remove the never-share text from non-owner prompts; label matching facts owner-only when written; add an output check; seed canary memories at each tier in `tests/redteam/` | No canary appears in any prompt or reply at a lower tier, using the fake model | US$0 |
| 2 | Full-text retrieval: a generated `tsvector` column on `memory_item`; a new `document_chunk` table; RRF query inside `memory_filter`; full-context mode for the owner below about 8,000 tokens; a 50–100-question owner evaluation with a fixed Haiku judge | Evaluation beats newest-30 retrieval; no fact from a lower audience is ever returned | About US$0.20 per evaluation run |
| 3 | Embeddings: `halfvec(1024)` columns and `embedding_model`; voyage-4-nano locally by default, voyage-4-lite key optional; backfill job; vector branch added to RRF; pgvector 0.8.4 or later | The evaluation improves again on paraphrased questions and code-mixed text | US$0–0.02 |
| 4 | Consolidation: `trust_rank`, `author_person_id`, `superseded_by`, `importance`, `status`, `conflict_status`; reconciliation job using cosine thresholds; conflict review in the notebook; link-sourced facts limited to about/work; versioned re-uploads; transcript document for the call; hard-erase cascade with a `derivation` table | A lower-trust claim never replaces an owner fact; erasing a file removes its text, chunks, facts and influence on the brief | About US$1 |
| 5 | Triage: inbound triage tables, Haiku schema, policy engine, take-a-message for strangers with per-sender and daily caps, VIP invite links, owner approval for unlinked claims | Red-team tests pass: a fake VIP on a new account, "URGENT wire" with a bank change, a stranger with a real deadline | About US$0.50 |
| 6 | Escalation and digests: the ladder with ack buttons on Telegram and Slack, two or three digests a day, feedback labels, weekly metrics; plus row-level security, log redaction, rotation reminders, and a first-contact AI notice | The owner can audit every interrupt and every handled item from the digest | About US$0.50 |

The chat model, not the memory machinery, decides whether the budget holds. A Sonnet 5.5 turn costs about **US$0.009**, assuming roughly 3,000 uncached input tokens at US$2 per million, 2,000 cached at US$0.10, and 300 output tokens at US$10. A Haiku 5.5 turn costs about **US$0.0007** ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Adaptive thinking adds output tokens to both, so measure it.

Run development chat on Haiku through `MODEL_CHAT` and keep tests on the fake model. Then a plan of about 2,000 Haiku extractions (US$0.70), 1,500 triage classifications including resamples (about US$0.50), ten evaluation runs (US$1–2) and a final smoke test of 300 Sonnet turns (about US$2.70) totals roughly **US$5–6, leaving a US$4 reserve**. One thing to avoid is running the full LoCoMo benchmark on Sonnet: its 1,540 questions at about 7,000 tokens each would cost about US$22 in input alone, and the score would not tell you whether Ava serves its owner.

## Conclusion

For a single-owner assistant, the memory problem is mostly about authorisation and provenance, not retrieval technology. In Ava's design, a vector is just a column, and hybrid search is one SQL statement that wraps a filter Ava already enforces. The decisions that determine whether Ava is safe and useful are made elsewhere: which audience a fact may reach, which source may replace another, what a third party's text may trigger, and when a message deserves an interruption. All of them follow the same pattern, in which Claude proposes structured evidence and code decides. That consistency is a practical asset. It makes every safety property testable with the fake model at zero cost, and it means the US$10 cap is spent on conversation quality rather than on memory bookkeeping. The one place the current build departs from the pattern, the never-share list sent to colleagues inside a prompt, is the clearest illustration of why the pattern matters.

The less obvious implication links this work to Ava's developmental stages. The owner's corrections fall into three kinds: "should have interrupted", "not urgent", and "which is current?". Together they form a labelled, owner-specific record of whether Ava judges well, which no public benchmark provides. Interrupt recall and precision, conflict-resolution accuracy, and canary runs without leaks are exactly the measured, revocable milestones that could promote Ava from Infant to a stage where some actions run without approval. They could also demote it automatically when the numbers slip. Build the triage log and the feedback buttons first, and the growth curve comes almost for free.
