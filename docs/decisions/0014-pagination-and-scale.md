# 0014: Pagination and handling data at scale

Status: accepted 2026-10-23. Part 1 (this audit, the backend helper, the frontend standard) lands first; each endpoint then moves to the helper in its own PR (section "Rollout").

## Context

The owner's requirement: proper pagination and skeleton loaders everywhere; the UI can never try to show gigabytes of data; handling data at scale is a core focus.

Today there is no pagination. Lists are either unbounded (`.all()` on a whole table) or cut by a hard-coded `LIMIT` that silently hides everything older (`/app/notebook` shows the newest 300 facts and nothing says the rest exist; `/app/messages` shows the newest 200 and the chat never reaches older ones; PR #318 adds `before=<id>` to that one route). There is no cursor, no `has_more`, no skeleton that matches the content, and no index that matches any list's `ORDER BY`, so even the capped lists sort every row of the owner's instance before cutting.

The audit below was read from the code on `origin/main` at `3009ac06` (migration head 0075). "At 1M rows" means the table holds a million rows for the instance (or, for operator routes, in total), which a long-lived owner reaches with `event`, `audit_log`, `spend_entry` and `skill_receipt` first.

## Part A: the audit

### A1. Every list-returning route

Columns: **Limit** is what the code does now. **Order** is the `ORDER BY`. **Index** says whether one supports that order for the filter used (all tables have single-column indexes on `tenant_id` and `agent_id` from `Scoped`; none has a `created_at` index, so "none" means a sort of every matching row). **1M rows** is the worst case of one response today. **Page** is the frontend consumer.

#### Owner routes (`/app/*`, cookie session)

| Route | Limit now | Order | Index | Shape | 1M rows | Consumer |
|---|---|---|---|---|---|---|
| `GET /app/messages` | `limit` 1..500, default 200, newest N returned oldest first (#318 open: `before=<id>`) | `event.created_at desc` | `context_id` only, no `(context_id, created_at, id)` | bare array | 500 events with `meta.activity`/`reasoning`: ~1 to 2 MB per response; the sort reads every event of the context | `ChatPage`, `Home` (`/messages` whole), `Meet` (onboarding) |
| `GET /app/notebook` | hard `LIMIT 300`, no cursor | `memory_item.created_at desc` | none (GIN on text only) | `{memories: [...]}` | 300 rows, ~100 KB; older facts unreachable; sorts every live fact of the owner | `NotebookPage`, `Home` |
| `GET /app/notebook/conflicts` | none | n/a | none | `{conflicts}` | unbounded but only open conflicts (small by design) | `NotebookPage` |
| `GET /app/people` | none | `contact.vip desc, person.display_name` | none | `{people}` | **unbounded + N+1** (one `channel_identity` query per contact): 5k contacts = 5k queries | `PeoplePage`, `Home` |
| `GET /app/reminders` | `upcoming(limit=100)` in two halves (soonest, then went-off) | `due_at` / `due_at desc` | `ix_intention_owner_status_due` (good) | `{timezone, reminders}` | 100 rows; with `?all=true` the closed ones past 100 are unreachable | `RemindersPage`, `Home` |
| `GET /app/approvals` | hard `LIMIT 50`; also loads **every** `person` of the instance for names | `approval.created_at desc` | none | bare array | 50 rows, but the names query is a full `person` read | `Approvals`, `ApprovalsPage` |
| `GET /app/needs-you` | `LIMIT 300` then filtered in Python | `triage_decision.created_at desc` | none | `{...}` | 300 rows read then thinned; page fill is unpredictable | `useNeedsYou`, `NeedsYouPage` |
| `GET /app/access-log` | `limit` 1..500, default 100 | `audit_log.created_at desc` | `ix_audit_log_action` only; filter on `actor`+`action` JSON-free columns then sort | bare array | 500 rows; scans every `operator_access` row of the instance | `AccessLogCard` |
| `GET /app/episodes` | `LIMIT 100` | `episode.started_at desc` | none | `{episodes}` | 100 rows; older unreachable | `EpisodesPage`, `NotebookPage` |
| `GET /app/intro/documents` | none | `document.created_at` | none | bare array | **unbounded + N+1** (one `count(*)` per document) and each row loads `Document.text` (up to 10 MB each) just to show a title | `NotebookPage`, `Meet` |
| `GET /app/playbooks` | none | `created_at desc` | none | bare array | unbounded (owner writes few, but unbounded in code) | `SkillsPage` |
| `GET /app/tuner` | `LIMIT 30` | `version desc` | none | `{...}` | 30 | `SettingsPage` |
| `GET /app/updates` | `LIMIT 30` | `created_at desc` | none | `{...}` | 30 | n/a (UI soon) |
| `GET /app/receptionist/messages` | `LIMIT 200` | `created_at desc` | none | `{...}` | 200 | `ReceptionistCard` |
| `GET /app/rehearsal` | `LIMIT 50` | `created_at desc` | none | `{sessions}` | 50 | `RehearsalPage` |
| `GET /app/school` | `LIMIT` constant | `created_at desc` | none | `{...}` | bounded | `useSchool` |
| `GET /app/school/export` | none | `created_at` | none | file/JSON | unbounded export | n/a |
| `GET /app/skills`, `/app/skill-releases`, `/app/skill-signals` | none | release time | none | bare/`{...}` | small catalog; `skill-releases` unbounded over time | `SkillsPage`, `SkillReleasesPanel` |
| `GET /app/skill-receipts` | **loads the whole append-only chain 3 times** (`count`, `verify`, `tiers`) | `seq` | PK / unique seq | `{count, chain, tiers}` | 1M receipts = 3 full reads and an O(n) hash walk on every call; the chain only grows | n/a |
| `GET /app/skill-receipts/export` | none | `seq` | n/a | one JSON document | the full chain in one response and one Python list | n/a |
| `GET /app/passport/export` | none (import is capped at 5000 facts, export is not) | `created_at, id` | none | zip, built in memory | all owner facts + all documents' full text in RAM | `PassportPanel` |
| `GET /app/passport/imports` | none | newest first | none | `{imports}` | small | `PassportPanel` |
| `GET /app/mandates` | none | `created_at` | none | `{mandates}` | small by design | `MandatesCard` |
| `GET /app/ask-link/requests` | `limit` 100 inside `open_requests` | `created_at desc` | none | `{...}` | 100 | `AskLinkCard` |
| `GET /app/memory-socket` | `LIMIT 100` (log) | `audit_log.created_at desc` | none | `{...}` | 100 | n/a |
| `GET /app/principal-card/sent` | list kept to `MAX_SENT = 200` in state | newest | n/a | `{cards}` | 200 | `usePrincipalCard` |
| `GET /app/brain` (glass brain) | facts `MAX_FACTS=200`, superseded 50, forgotten 50 | `created_at desc` | none | one graph document | bounded; recomputed whole each load | `BrainPage` |
| `GET /app/brain/share-links`, `/push/devices`, `/channels/linked`, `/identity-claims`, `/import-leases`, `/standing-orders`, `/fork-night`, `/journal`, `/mirror`, `/twin`, `/updates`, `/audition/candidates`, `/model-drill`, `/playbooks` | none | small | n/a | bounded by product rules (`MAX_ACTIVE`, `MAX_RULES`, a handful of devices); `/identity-claims` and `/import-leases` are unbounded in code | Settings and card components |
| `GET /app/agents`, `/app/providers`, `/app/connections`, `/app/onboarding`, `/app/me`, `/app/preferences` | fixed small sets | n/a | n/a | n/a | fixed | catalog, settings |

#### Operator console routes (`/api/*`, bearer `ADMIN_TOKEN`; content routes run inside one instance after a break-glass reason, so "unbounded" below means one instance's whole table, not the platform's)

| Route | Limit now | Order | Index | 1M rows | Consumer |
|---|---|---|---|---|---|
| `GET /api/events` | `limit` 1..500, default 100, optional `context_id` | `event.created_at desc` | `context_id` only | 500 rows; sort reads all events (all instances when no `context_id`) | console `Conversations` (polls every 3 s with `limit=200`) |
| `GET /api/memories` | **none** | `created_at desc` | none | **every memory of the instance**: ~0.4 KB each = ~400 MB JSON, plus the `person` read | console `Memories` |
| `GET /api/approvals` | **none** | `created_at desc` | none | every approval, plus every `person` | console `Approvals` |
| `GET /api/people` | **none** | `created_at` | none | loads **all** persons, **all** channel identities and **all** link events, joins in Python | console `People`, `Simulator` (polls every 15 s) |
| `GET /api/conversations` | **none** | `max(event.created_at) desc` | none | `GROUP BY context` over **all** events: a full scan of `event` for every poll | console `Conversations` |
| `GET /api/audit` | `limit` 1..1000, default 200 | `audit_log.created_at desc` | none | 1000 rows; sorts the whole log | console `SafetyLog` (`limit=300`) |
| `GET /api/spend` | `LIMIT 200` plus summary aggregates | `created_at desc` | none | summary is an aggregate over all `spend_entry` rows | console `SpendPage` |
| `GET /api/instances` | none | `created_at` | none | one row per hired agent: unbounded as customers grow | console `BreakGlass` (polls every 30 s) |
| `GET /api/overview` | aggregates | n/a | n/a | `count(*)` over big tables on every load | console home |

### A2. Other unbounded things

| Area | What happens | Risk at scale |
|---|---|---|
| SSE (`/app/chat/stream`) | Per-turn stream with a queue and 15 s keep-alive. The turn is detached from the connection. Events are small and per turn, not a history feed. | Low. Needs a cap on concurrent streams per owner and on queue length; not in this ADR. |
| Exports | Passport export builds the zip fully in memory from all facts and all document bodies; skill-receipts export returns the whole chain; school export. | High for `skill_receipt` and passport. Stream the response or page by `seq`/`id` and write rows as they arrive. |
| Recall | `recall_*` take `limit` 6 to 8, each arm fetches `2 x limit`, fused; chunk recall 4. | Low: bounded by construction, GIN/pgvector indexed. Keep. |
| Prompt and context assembly | `history` for a turn is a bounded window; pinned facts `PINNED_MAX`; recall capped. | Low. A per-prompt character budget is the remaining guard and already exists in `brain/turn.py`. |
| File/link ingestion | Upload cap 10 MB (`extract.MAX_FILE_BYTES`), web fetch 2 MB / 12 000 chars, extraction reads the first 12 000 characters, passport import caps (25 MB zip, 5 000 facts). | Medium. The cap is on bytes in; extraction truncation is silent (known gap, see CLAUDE.md). Chunk reads by `document_id` are ordered by `ordinal` and unbounded for a huge document. |
| Document chunk reads | `store_chunks` reads every chunk of one document, twice. | Medium for a 10 MB document (thousands of chunks). Read in batches. |
| Tool results | Gmail search `SEARCH_LIMIT`, message body `MAX_BODY=1500`, web `MAX_TEXT`. | Low: capped before the model sees them. |
| In-memory loads | `.all()` over a whole table: `person` (names map) in `/app/approvals`, `/api/memories`, `/api/approvals`; `select(MemoryItem).where(memory_filter(...)).all()` in `proactive/digest.py` (the owner's whole notebook, to find dates, every morning); `skills/signals.py` and `skills/registry.py` scans; `receipts.rows` (whole chain). | High for the digest, receipts and the operator routes. |
| N+1 | `/app/people` (identities per contact), `documents_view` (count per document), `/api/people` (loops in Python over three full tables, no N+1 but no bound). | Medium to high. Replace with one grouped query per page. |
| Polling | Console polls `/api/events` (3 s), `/api/people` (15 s), `/api/instances` (30 s) with unbounded or sorting queries. | Medium: the cost of a slow query is multiplied by the poll rate and by open tabs. |

### A3. Ranked by risk

Risk = (probability it grows large) x (cost per request) x (how often it is called), highest first.

1. **`/api/memories`, `/api/people`, `/api/approvals`, `/api/conversations` (operator).** No bound at all across every instance; polled; a single call can read the whole database. Fix: keyset pages with `limit <= 100`; `conversations` needs a maintained `last_event_at` on `context` (or a keyset on `context.created_at`) instead of a full `GROUP BY`.
2. **`/app/messages` (chat history) and `Home`'s call to it.** Highest volume table, hit on every dashboard load, silently truncated, no `(context_id, created_at, id)` index. Fix: keyset by `(created_at, id)`, composite index, #318's `before=<id>` becomes the cursor.
3. **`/app/skill-receipts` and `/export`.** The append-only chain is read whole three times per overview. Fix: aggregate in SQL or keep running tiers; page the export by `seq`.
4. **`/app/notebook` (and `Home`).** The largest per-owner list the UI shows; capped at 300 with no way to the rest. Fix: keyset on `(created_at, id)` with a partial index on live, non-conflicted facts.
5. **`/app/intro/documents`.** Unbounded, N+1, loads the full text of every document. Fix: defer `Document.text`, one grouped count, page.
6. **`/api/audit`, `/app/access-log`, memory-socket log.** Append-only; sort of the whole log on each call. Fix: keyset plus `(agent_id, created_at desc, id desc)` index; operator `/api/audit` keeps `limit<=200`.
7. **`/app/people`.** N+1 and unbounded; fix with one grouped identity query and a page.
8. **Passport export, school export.** Whole export in RAM; stream.
9. **`proactive/digest.py` whole-notebook scan.** Select only the columns and rows that can carry a date, or index the extracted date.
10. **Reminders, approvals, needs-you, episodes, receptionist, rehearsal.** Capped small lists that hide older rows. They need a cursor, not a bigger cap.
11. The rest: small by product rules. They keep their shape and are listed so a new unbounded one is noticed in review.

### A4. Index gap

No list has an index that matches its `ORDER BY`. The shared guidance is in Part B. The concrete composites (one migration each, owner assigns the number) are:

| Table | Index | Serves |
|---|---|---|
| `event` | `(context_id, created_at desc, id desc)` | `/app/messages`, `/api/events?context_id=` |
| `event` | `(agent_id, created_at desc, id desc)` | `/api/events` without a context |
| `memory_item` | `(agent_id, created_at desc, id desc) WHERE valid_to IS NULL` | `/app/notebook`, `/api/memories` |
| `audit_log` | `(agent_id, created_at desc, id desc)` | `/app/access-log`, `/api/audit` |
| `approval` | `(agent_id, created_at desc, id desc)` | `/app/approvals`, `/api/approvals` |
| `document` | `(owner_person_id, created_at, id) WHERE removed_at IS NULL` | `/app/intro/documents` |
| `contact` | `(owner_person_id, vip desc, display key, id)` | `/app/people` (needs the name on `contact` or a denormalised sort key) |

## Part B: the standard

### Decision

Every list that can grow is paginated with **keyset (cursor) pagination**. Offset pagination is not used: it costs O(offset) and skips or repeats rows when data is inserted while someone pages.

**Request.** `GET /path?limit=25&cursor=<opaque>` plus the endpoint's own filters. `order` is fixed per endpoint (newest first unless the page is naturally chronological) and is not a client parameter, so a cursor and an index always agree on the sort. `limit` default 25 (50 for dense lists such as chat), hard maximum 100 (200 for the operator console and chat). Outside the bounds, the answer is 422.

**Response.**

```json
{ "items": [ ... ], "next_cursor": "v1.eyJ...", "has_more": true, "total_estimate": null }
```

- `next_cursor` is null on the last page; `has_more` is the same fact as a boolean. The server reads `limit + 1` rows to know, never `COUNT(*)`.
- `total_estimate` is present only where it is cheap: Postgres' planner estimate for operator tables (`approx_count`), or an exact count that an index answers in microseconds for a bounded set. It is never an exact `COUNT(*)` on a big table, and the UI says "about".
- The cursor is opaque. Clients never build one.

**Cursor.** `v1.<base64url payload>.<base64url HMAC-SHA256>`. The payload holds the sort-key values of the last row returned (timestamp, id), the endpoint name, a binding string (`<agent_id>:<owner person id>`), the sort signature and an expiry. The key is derived from the vault master key with a purpose label, so it is never the master key itself. Decoding checks, in order: size, shape, signature (constant time), expiry, endpoint, binding, sort signature. Any failure is **422** with the standard error envelope and `error.code = invalid_cursor` (`invalid_limit` for a bad limit). A cursor therefore cannot be edited, replayed on another endpoint, replayed by another owner or another instance, or reused with a different order. It also grants nothing: the query's tenant, owner and audience filters are part of the statement before the cursor condition is added, so a valid cursor can only narrow what the caller may already see.

**Ordering.** The sort always ends in a unique column (`id`) so rows sharing a timestamp have a total order, and the keyset condition compares the whole tuple: `(created_at, id) < (:t, :id)`. When every key goes the same way this is a single row comparison that a btree index on those columns answers by seeking. Mixed directions expand to an equivalent `OR` form. Sort columns must be NOT NULL.

**Concurrent writes.** A page boundary is a position in the sort order, not a count of rows. A row inserted before the boundary appears at the top of a refreshed list and never duplicates a row below; a row deleted never shifts the next page. (Tested.)

### Composition with the API envelope (ADR 0013)

Pagination metadata lives **inside `data`**, so that the envelope stays `{status, status_code, message, data, error}` and a paginated payload is just a payload:

```json
{ "status": "success", "status_code": 200, "message": "OK",
  "data": { "items": [...], "next_cursor": "...", "has_more": true, "total_estimate": null }, "error": null }
```

Errors are the existing error envelope (`invalid_cursor`, `invalid_limit`, `validation_failed`). Until ADR 0013 phase 2 wraps successes, a migrated route returns the `Page` object itself as the body; phase 2 wraps it without touching the route. A bare-array route that must not change shape yet keeps its array and gains the next page in headers (`X-Next-Cursor` and `Link: <url?cursor=...>; rel="next"`) via `set_next_headers`. A dict-shaped route (`{memories: [...]}`) keeps its key and gains `next_cursor` and `has_more` beside it. Both are additive; old clients ignore them. Once every consumer reads the new fields the old shape is dropped, in the same release as the ADR 0013 `detail` removal.

### Compatibility mode for routes that answer a bare array (`/app/messages`)

A route whose body is a bare JSON array (the chat history, which existing clients and e2e stubs read as an array) keeps that body. Paging rides in headers instead: `X-Has-More: true|false`, `X-Next-Cursor` and `Link: <url?cursor=...>; rel="next"` (the last two only when there is another page). The `cursor` query parameter is the standard signed cursor. A legacy anchor parameter (`before=<id>`, from #318) keeps working: the server turns it into the same keyset position a cursor holds (`(created_at, id)` of that row, verified to belong to the caller's conversation), so a client that tracks "the oldest message I have" (the chat does, because its visible window moves as messages are sent) needs no stored cursor. `limit` is bounded 1 to 200 and a value outside answers 422 instead of being clamped silently. Once every consumer reads the new shape, the array body goes with the ADR 0013 `detail` removal.

### Indexes

- Every paginated list has an index whose columns are the equality filters followed by the sort keys in the sort direction: `(context_id, created_at desc, id desc)`. A leading `agent_id` is added when the route is not already narrowed by an indexed owner column, because the instance scope is always in the `WHERE`.
- A filter that selects a minority of rows (`valid_to IS NULL`, `removed_at IS NULL`) becomes a partial index with the same condition, so the index stays small as history grows.
- A test per migrated route runs `EXPLAIN (FORMAT JSON)` on the page query for the first page and a deep page over a seeded table and fails if the plan contains a `Sort` or a `Seq Scan`. `tests/pagination/test_pagination.py` does this for the helper's query shape on a probe table with the same index shape; each endpoint PR adds the real table.
- Migrations that only add an index are their own PR, one at a time, and use `CREATE INDEX CONCURRENTLY` in production-sized tables.

### Limits as configuration

`PAGINATION_CURSOR_MAX_AGE_S` (default 86400) bounds how long a cursor is accepted. Maximums are constants in code (`HARD_MAX_LIMIT = 200`), not settings, so no environment can make a page unbounded.

## Part C: the frontend standard

- `frontend/src/lib/paginated.ts`: `usePaginated<T>(path, { limit, params, fetcher })` (state machine in `lib/paginated-core.ts`, testable without React) owns the cursor state. It returns `items`, `status` (`loading` first load, `ready`, `refreshing`, `loading-more`, `error`), `error`, `hasMore`, `loadMore()`, `reset()`, `retry()`. A change of `path` or `params` resets to page one and aborts the request in flight. Stale-while-revalidate: while a reload is running the previous items stay on screen. Requests are aborted on unmount and on reset; a page is merged by `id`, so a retried or overlapping request cannot duplicate a row.
- `frontend/src/components/ui/skeleton.tsx` gains `SkeletonLine`, `SkeletonBlock`, `SkeletonRow`, `SkeletonCard`, `SkeletonTableRows` and `SkeletonList`, each taking the dimensions of the content it stands in for so the page does not jump when data arrives. The shimmer is a CSS animation that stops under `prefers-reduced-motion`.
- `frontend/src/components/ui/list-footer.tsx`: `ListFooter` shows Load more, "End of list" or an error with Retry, announced to screen readers.
- `useInfiniteScroll` (`lib/use-infinite-scroll.ts`, an `IntersectionObserver` on a sentinel) is optional; the button stays as the keyboard path.
- Long lists render through `WindowedList` (`components/ui/windowed-list.tsx`, arithmetic in `lib/windowing.ts`) written in-house: fixed or measured row height, an overscan, a spacer above and below. The page stays under a few dozen DOM nodes whatever the loaded size. Justification: the need is a vertical list with variable but cacheable height; adding a virtualisation dependency brings a large API for a feature used on two or three screens, and the in-house hook is under 100 lines and fully tested.
- The operator console reuses these from `components/ui`; it does not define its own.

## Rollout

1. This PR: ADR, `pagination.py`, `PAGINATION_CURSOR_MAX_AGE_S`, helper tests (roundtrip, tamper, replay, bounds, ordering, concurrent writes, EXPLAIN, scale).
2. Frontend standard PR: `paginated.ts`, skeleton and footer primitives, windowing, vitest and Playwright.
3. One PR per endpoint, in the order of the risk ranking above, each with its index migration, its EXPLAIN test, its seeded scale test, the skeletons on its page and an e2e: operator `events` and `audit`, `/app/messages`, `/app/notebook`.
4. Then the operator `memories`/`people`/`approvals`/`conversations`, receipts, documents, people, and the exports.

## Consequences

- Every list has a page size and an index, and the UI cannot be handed more than 100 to 200 rows in one response.
- Old rows become reachable instead of silently hidden.
- A cursor is not a stable bookmark across a day; clients restart the list when they get `invalid_cursor`.
- Every migrated route costs a migration (an index) and a test; that is the point.

## Alternatives

- **Offset/limit.** Simple, but O(offset) and unstable under inserts. Rejected.
- **Exact `total` on every page.** `COUNT(*)` on a large table is a scan; rejected in favour of `has_more` plus an optional estimate.
- **Unsigned base64 cursors.** A client could forge a position or move one between endpoints and tenants. Rejected: the signature is cheap.
- **A virtualisation library (TanStack Virtual, react-window).** Good libraries, but heavier than the three screens need. Revisit if a list needs variable-height windowing with sticky groups.
- **Server-side cursors held open in the database.** Ties up a connection per reader; rejected.

## Update 2026-10-24: operator paging and `context.last_event_at`

- `/api/events` keeps its bare array and gains `cursor` plus the `X-Next-Cursor` / `Link` / `X-Has-More` headers (compatibility mode above), `limit` 1 to 200.
- `GET /api/conversations/page` lists conversations by last activity as a standard `Page`. The old `/api/conversations` stays until the console adopts the new route. Its `GROUP BY context` over every event is replaced by a maintained column, `context.last_event_at` (migration 0078, indexed with `(agent_id, last_event_at desc, id desc)`). It is written in one place, `memory.store.touch_context`, called by every code path that inserts an `Event` (`log_event` and the Gmail intake); the update uses `GREATEST` so an out-of-order or backfilled event never moves it backwards; a test fails if a new `Event(` appears anywhere else. A conversation's message count is read up to 1000 (`messages_more` beyond), never an exact count over a long conversation.
- Cursors on operator routes are bound to the endpoint (and, for events, the conversation filter) and to the instance in scope.
