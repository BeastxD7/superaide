# Authorization engines and permission-aware retrieval for room-scoped assistant memory

Research date: 2026-10-23. Convention: "Cited" = stated in a source I fetched. "Inference" = my reasoning. "Memory/unverified" = from my background knowledge, not confirmed in this session. Vendor marketing numbers are flagged VENDOR CLAIM.

## Sources read (what I actually read)

| Source | How read | Notes |
|---|---|---|
| Zanzibar paper (USENIX ATC '19), https://storage.googleapis.com/gweb-research2023-media/pubtools/5068.pdf | Downloaded the PDF and extracted text locally (68 KB of text); read abstract, 2.1-2.4, 3.2.x, 4.x. Not every section in full (skipped most of 3.2.2-3.2.5 detail and the section 5 lessons). | Primary. |
| Google Research landing page, https://research.google/pubs/zanzibar-googles-consistent-global-authorization-system/ | Abstract only | |
| OpenFGA docs: concepts, configuration language, configure-openfga, list-objects, entitlements tutorial; GitHub README | Pages fetched through a summarising tool (not raw text) | Summaries only; some pages did not cover what I asked. |
| SpiceDB docs: schema, consistency, CLI flag reference (`concepts/commands`); GitHub README | Same summarised fetch | The "commands" page turned out to be a flag reference. |
| Permify GitHub README | Summarised fetch | The docs site redirected to fusionauth.io/permify-docs and that 404'd. |
| Ory Keto product page + GitHub README | Summarised fetch | Thin. |
| Cedar docs (authorization algorithm, terminology), AWS Verified Permissions terminology | Summarised fetch / full page | |
| Cerbos API reference | Summarised fetch | |
| OPA policy-performance page | Summarised fetch | |
| Casbin overview, Oso docs home | Summarised fetch | Thin. |
| Microsoft Copilot privacy page (learn.microsoft.com, updated 2026-10-08) | Full page returned | Primary vendor doc. |
| Slack AI help article | Summarised fetch | |
| Glean | Direct page 404'd; web-search summary only | Weakly sourced. |
| pgvector README (filtering) | Summarised fetch | |
| Auth0/Okta FGA RAG blog | Summarised fetch | |
| SpiceDB CVEs | Web-search summary only (secondary) | Not read at the advisory source. |

Not obtained: OpenFGA and SpiceDB independent benchmarks (none found; see Gaps), Microsoft Graph search-trimming internals, Slack Connect behaviour in Slack AI, Glean primary docs, Cerbos/Oso/Cedar/OPA licenses (see the table, marked memory).

---

## 1. What the engines are and how they compare

### Takeaway
Every Zanzibar-family engine (Zanzibar, OpenFGA, SpiceDB, Permify, Keto) answers the question "may subject S do R on object O" for one subject at a time and supports union, intersection and exclusion. None of them has a native "may EVERY member of room R receive fact F" operator. That is a universal quantifier over a changing set, and it is best computed as a set difference ("room members minus people who may know F must be empty"). That is trivial in SQL and awkward in a ReBAC engine.

### Cited Findings

**Zanzibar (primary)**
- Tuples have the form `object#relation@user`, for example `group:eng#member@11` and `doc:readme#viewer@group:eng#member` (usersets as subjects). Groups are "simply ACLs with membership semantics." — [Zanzibar paper](https://storage.googleapis.com/gweb-research2023-media/pubtools/5068.pdf), section 2.1, Table 1.
- Userset rewrite rules leaves: `this`, `computed_userset`, `tuple_to_userset`. "A userset expression can also be composed of multiple sub-expressions, combined by operations such as union, intersection, and exclusion." — same, section 2.3.
- APIs: Check, Read, Write, Watch, Expand. Expand "returns the effective userset" and is "crucial for our clients to... build efficient search indices for access-controlled content." — same, 2.4.5. This is the paper's own endorsement of materialising ACLs into a search index.
- Read returns only stored tuples, not rewrite results. Write uses optimistic concurrency with a per-object "lock" tuple. — same, 2.4.1-2.4.2.
- Zookie: "an opaque byte sequence encoding a globally meaningful timestamp." The client stores a zookie with each content version (obtained via a content-change check) and sends it with later checks so the check snapshot is "at least as fresh as" the content. This prevents the "new enemy" problem (ACL change A1 removing Bob, then new content added, must never be evaluated against the pre-removal ACL). Without a zookie Zanzibar uses a default staleness. — same, 2.2, 2.4.
- Scale: "more than 2 trillion relation tuples that occupy close to 100 terabytes"; 1,500+ namespaces; median namespace about 15,000 tuples; peak Check 4.2M QPS, Read 8.2M, Expand 760K, Write 25K; 10,000+ servers. "Responds to more than 95% of authorization checks within 10 milliseconds"; availability above 99.999% over 3 years. Check (Safe) latency peaks about 3 / 11 / 20 / 93 ms at p50/p95/p99/p99.9 over 7 days. Leopard index: 1.56M QPS median, answers in under 150 microseconds at median and under 1 ms at p99. Deeply nested groups and groups with many members needed Leopard denormalisation. — same, sections 1, 3.2.4, 4. Google-internal numbers on Spanner; not transferable to a 1-server Postgres.

**OpenFGA**
- Apache-2.0; datastores in-memory (dev only), PostgreSQL 14+, MySQL 8, SQLite (beta). README gives no benchmark or latency figures. — [GitHub](https://github.com/openfga/openfga)
- Check, ListObjects ("all objects of a given type that a user has a specified relationship with"), ListUsers, contextual tuples on requests, conditions as named CEL expressions evaluated when the tuple is read. — [OpenFGA concepts](https://openfga.dev/docs/concepts)
- DSL operators: `or`, `and`, `but not` ("useful for block lists"), `X from Y` (tuple-to-userset), parentheses, wildcards `type:*`, conditions. Limits: the tupleset after `from` cannot itself be a computed relation; wildcards/usersets not allowed in `from` type restrictions. — [Configuration language](https://openfga.dev/docs/configuration-language)
- ListObjects: performance "vary drastically depending on the model complexity, number of tuples"; relations using `and` or `but not` are more expensive than `or`; streamed variant bounded by `OPENFGA_LIST_OBJECTS_DEADLINE`. No numeric cap or default stated on the page. — [ListObjects guide](https://openfga.dev/docs/getting-started/perform-list-objects)
- Store isolation: "store data can't be shared across stores" (natural tenant boundary). — [Concepts](https://openfga.dev/docs/concepts)
- Postgres supports an optional read replica; `HIGHER_CONSISTENCY` reads go to the primary. Experimental flags: `check-query-cache`, `enable-check-optimizations` (1.6.2+). — [Configure OpenFGA](https://openfga.dev/docs/getting-started/setup-openfga/configure-openfga)
- CNCF project status: not on the pages I fetched. From memory (unverified): CNCF incubating.

**SpiceDB (AuthZed)**
- Apache-2.0; datastores Spanner, CockroachDB, MySQL, PostgreSQL; 7.2k stars. VENDOR CLAIM: "5ms p95 when scaled to millions of queries/s, billions of relationships," in production at Authzed since 2021. — [GitHub](https://github.com/authzed/spicedb)
- Schema: `relation`, `permission`, union `+`, intersection `&`, exclusion `-`, arrow `->`, `.all()` (intersection arrow: "requires the permission on every related object"), wildcards, caveats (named boolean expressions evaluated at check time). Warnings: "Intersection arrows can impact performance since they require loading all results for the arrow"; "Be very careful with wildcard support"; `+` binds tighter than `&` and `-`. — [Schema docs](https://authzed.com/docs/spicedb/concepts/schema)
- Consistency: `minimize_latency` (default for most APIs; leaves a window for the New Enemy Problem), `at_least_as_fresh` (ZedToken), `at_exact_snapshot` (can fail "Snapshot Expired" after GC window), `fully_consistent`. Writes use fully_consistent. ZedTokens are the Zookie equivalent, returned by CheckPermission, WriteRelationships, DeleteRelationships; store them next to the protected resource. Revision quantization trades freshness for cache hits. — [Consistency docs](https://authzed.com/docs/spicedb/concepts/consistency)
- Lookup limits: `--max-lookup-resources-limit` default 1000 resources per request; streaming APIs; Watch API with a bounded buffer that can error and need restart; `--max-caveat-context-size` 4096 bytes. — [Flag reference](https://authzed.com/docs/spicedb/concepts/commands)
- Correctness history of Lookup with negation (secondary source, web-search summary of CVE databases, not read at the advisory): LookupResources could wrongly list a resource when a wildcard sat inside an exclusion (CVE-2022-21646, fixed 1.4.0); caveats combined with intersection/exclusion could return HAS_PERMISSION instead of CONDITIONAL/NO_PERMISSION via the check cache (CVE-2026-55866, fixed 1.54.0); a union bug could omit LookupResources results before 1.47.1. — [search summary](https://advisories.gitlab.com/pkg/golang/github.com/authzed/spicedb/) (verify at advisory pages before relying on it).

**Permify**
- AGPL-3.0; "acquired by FusionAuth"; docs moved to fusionauth.io/permify-docs. A self-reported load test targeted 10,000 RPS but achieved about 738 req/s with 272,433 dropped iterations; median 15.38 ms, p95 58.99 ms. VENDOR-published, and it shows the target was not sustained. — [GitHub](https://github.com/Permify/permify)

**Ory Keto**
- Apache-2.0; Postgres, MySQL, CockroachDB; "first open-source implementation" of Zanzibar; the Ory Permission Language (OPL) is a TypeScript subset. The README does not say whether OPL ships in the open-source build and mentions an enterprise license adding unnamed features. Negation, expand and list not mentioned on the pages fetched. — [Keto page](https://www.ory.com/docs/keto), [GitHub](https://github.com/ory/keto)

**Cedar / Amazon Verified Permissions**
- Default deny; any satisfied `forbid` overrides any `permit`; erroring policies are skipped but reported. Groups and hierarchies via `in`. Policy stores can isolate tenants. Policy scope narrows considered policies for performance. — [Cedar authorization](https://docs.cedarpolicy.com/auth/authorization.html), [AVP terminology](https://docs.aws.amazon.com/verifiedpermissions/latest/userguide/terminology.html)
- Cedar is a policy evaluator: the caller supplies entity data (group membership, attributes) per request; it is not a tuple store. (AVP text: "Entity data relevant for policy evaluation are group membership all the way up the entity hierarchy and attribute values.") Residual/partial-evaluation and license were not on the pages; Apache-2.0 from memory (unverified).

**Cerbos**
- `CheckResources` (allow/deny per action) and `PlanResources` returning KIND_ALWAYS_ALLOWED / ALWAYS_DENIED / CONDITIONAL with an AST that adapters convert into query filters (Prisma, Drizzle, SQLAlchemy). Deny effects exist as EFFECT_DENY. License not on the page. — [Cerbos API](https://docs.cerbos.dev/cerbos/latest/api/index.html)

**OPA / Rego**
- Memory grows with data: "8MB of JSON for 100,000 permission objects needs roughly 160MB of RAM"; about 130 MB for 10,000 ACL-style rules and about 1.1 GB for 100,000. Prefer objects keyed by ID; partial evaluation turns policies into linear form. — [OPA performance](https://www.openpolicyagent.org/docs/policy-performance)

**Casbin**
- Apache-2.0 (Apache Incubator); models ACL, RBAC, ReBAC, ABAC, etc.; policies "including both allow and deny"; storage via adapters; does not do authentication. — [Casbin overview](https://casbin.apache.org/docs/overview/)

**Oso**
- Oso Cloud: a centralized authorization service built on Polar and SQLite; returns a boolean, a list, or "logic to run against your database"; "Local Authorization" evaluates against your own DB. Open-source library status and licensing not on the page (I believe the original library was deprecated; unverified). — [Oso docs](https://www.osohq.com/docs)

### Comparison table

Legend: "native" means documented in this research; "?" means I did not confirm.

| Engine | Model power | Negation / deny | Consistency | Postgres fit | Ops burden | License | Latency evidence |
|---|---|---|---|---|---|---|---|
| Plain Postgres tuples + materialised ACL | Whatever you code; membership via recursive CTE; subset test via EXCEPT | Anything (SQL) | Strong, single transaction with the memory write | Native; same query as pgvector | None extra | n/a | Sub-ms to few ms indexed at our scale (inference) |
| Zanzibar (reference) | Tuples, usersets, rewrite, tuple-to-userset | Exclusion in rewrite trees | Zookies, external consistency | n/a (Spanner) | n/a (internal) | n/a | p95 about 10 ms (Google scale) |
| OpenFGA | Zanzibar-style + CEL conditions | `but not` | Consistency options exist; page I read did not detail | Postgres 14+ datastore | One stateless server + DB; store per tenant | Apache-2.0 | No benchmark found; ListObjects cost grows with `and`/`but not` |
| SpiceDB | Zanzibar-style + caveats, `.all()` | `-` exclusion; Lookup correctness bugs historically around negation | Best documented: ZedTokens, 4 modes, quantization | Postgres, CockroachDB, Spanner, MySQL | Server + DB (+ dispatch cluster at scale) | Apache-2.0 | VENDOR CLAIM 5 ms p95 at huge scale |
| Permify | Zanzibar-style | ? | snap tokens (not confirmed) | ? | Server | AGPL-3.0, now FusionAuth | VENDOR load test: p95 59 ms, target not met |
| Ory Keto | Tuples, OPL | ? | ? | Postgres | Server | Apache-2.0 (OPL scope unclear) | none |
| Cedar / AVP | ABAC+RBAC policies over hierarchy; no tuple store | `forbid` overrides `permit` | Caller passes entity data per request | None (stateless library) / AWS service | Library: none. AVP: AWS-managed | Apache-2.0 (memory) | n/a |
| Cerbos | Policy files, derived roles | EFFECT_DENY | Stateless | Query plan to SQL filter | Sidecar PDP | ? | n/a |
| OPA/Rego | General logic over JSON data | Anything | You load data (bundles/push) | Poor for large mutable data | Sidecar + data sync | Apache-2.0 (memory) | memory/size guidance above |
| Casbin | Model + policy files, adapters | allow and deny | Library, in-process | Adapter | Library | Apache-2.0 | n/a |
| Oso Cloud | Polar rules + facts | Polar supports negation (memory) | Service | Local Authorization against own DB | SaaS | proprietary service | n/a |

### Inferences
- The decisive structural fact is that every ReBAC engine decides per (subject, object) pair. Our question is quantified over a set of subjects (the room), so either the app loops Checks (N members x 1 fact, or batch) or it pulls the fact's effective audience (Expand / LookupSubjects) and compares sets. Either way the quantifier lives in application code or SQL, not in the engine.
- Dedicated engines pay off for deep, high-fan-out, frequently changing relationship graphs shared across many services. Our graph is shallow (Person, Room, Fact, Topic; membership depth 1-2) at small scale.

### Gaps
- No independent OpenFGA/SpiceDB benchmark found; only vendor claims.
- OpenFGA consistency and Watch equivalents (changes API) not confirmed from docs I read.
- Permify/Keto negation and consistency features not confirmed.
- Cedar/OPA/Cerbos/Oso licenses not confirmed from fetched pages.

---

## 2. Permission-aware retrieval pattern

### Takeaway
Pre-filtering inside the retrieval query using ACL metadata co-located with the data is the only pattern that keeps top-k quality and no leakage; post-filtering silently shrinks results, and ListObjects-then-filter is bounded by engine result limits. All three big enterprise products document query-time enforcement against the user's identity, but their internals are mostly not public.

### Cited Findings
- pgvector with approximate indexes applies WHERE after the index scan, so filtered queries can return fewer rows: with HNSW and `hnsw.ef_search` = 40, a condition matching 10% of rows yields about 4 matches on average. Remedies: `hnsw.iterative_scan` (e.g. `strict_order`), B-tree on the filter column, partial indexes for few distinct values, partitioning for many. — [pgvector README](https://github.com/pgvector/pgvector)
- Post-filter example: Auth0/Okta FGA's RAG sample retrieves first, then batch-Checks each document (`ClientBatchCheckItem` per doc) before it reaches the LLM. The article gives no numbers and does not discuss ListObjects pre-filtering. — [Auth0 blog](https://auth0.com/blog/building-a-secure-rag-with-python-langchain-and-openfga/)
- SpiceDB LookupResources is capped by default at 1000 results per request (configurable). — [flags](https://authzed.com/docs/spicedb/concepts/commands)
- Zanzibar authors say Expand exists so clients can "build efficient search indices for access-controlled content" (materialised ACL as index metadata). — Zanzibar paper 2.4.5
- Microsoft Copilot: "only surfaces organizational data to which individual users have at least view permissions"; "Semantic Index honors the user identity-based access boundary so that the grounding process only accesses content that the current user is authorized to access"; permissions given to external users via Teams shared channels are included. Honors sensitivity labels/IRM usage rights. — [Microsoft Learn](https://learn.microsoft.com/en-us/copilot/microsoft-365/microsoft-365-copilot-privacy). The page does not describe the trimming mechanism, caches or revocation latency.
- Slack AI: answers "only include information available to you"; private channels and DMs only if you belong; admin controls; the article does not address Slack Connect. — [Slack help](https://slack.com/help/articles/25076892548883-Manage-Slack-AI-for-your-workspace)
- Glean (web-search summary of Glean developer docs, not read directly): connectors mirror source-system permissions, and at query time the signed-in user's identity is evaluated against them; each document carries a permission definition; groups must come with memberships. A third-party blog (deepinspect.ai, not Glean) notes the indexed model has a revocation window equal to sync lag. — [Glean security principles](https://docs.glean.com/security/security-principles), [Indexing permissions](https://developers.glean.com/libraries/indexing-sdk/permissions)
- Azure AI Search supports query-time ACL/RBAC trimming by identity and group. — [Microsoft Learn](https://learn.microsoft.com/en-us/azure/search/search-index-access-control-lists-and-rbac-push-api) (search-result mention only).

### Inferences
- All documented enterprise systems are single-requester models: the question is "what may this user see." None documents a multi-party "everyone in the room" rule. Our requirement (intersection over current members) is therefore ours to design; I found no vendor doc covering it.
- Large ACLs: the metadata approach (store audience on the row) is cheap when the audience is a handful of room/person ids; when a fact's audience is a big group, store group ids (usersets) not expanded members, and expand at query time via membership join. At our scale (thousands of facts, tens of rooms) expanded ACL rows are tiny.
- Staleness/revocation: materialised ACLs lag source changes unless updated in the same transaction. In single-DB Postgres the lag can be zero; with an external engine it is the Watch/sync lag (SpiceDB ZedToken or `fully_consistent` fixes reads but not the materialised copy).

### Gaps
- Microsoft Graph connector/Copilot revocation latency, Glean sync latency, and Slack Connect handling are undocumented in what I fetched.
- No benchmark comparing pre- vs post-filter recall for ACL-filtered vector search beyond the pgvector README example.

---

## 3. Semantics: "a fact may be said in a room"

### Takeaway
Define the rule as: a fact may be disclosed to a room iff no current member of the room is a "blocker," where blockers = current members (including unresolved/external/bot) minus people who may know the fact. Implement as exclusion/set difference, not as a positive-grant intersection, because it fails closed for unknown members.

### Inferences (design, no source covers this exact problem)
- Per-person right-to-know for F: `know(p, F) = (p in audience(F) OR F is public) AND NOT barred(p, F)`; `audience(F)` is a snapshot of people who were present/authorised when F was learned (or owner-only if learned privately).
- Room rule: `blockers(R, F) = members_now(R) \ { p : know(p, F) }`. Allow only if blockers is empty. Mathematically identical to "intersection of all participants' rights," but the exclusion formulation (a) makes the blocker list available for the "ask owner" prompt, (b) naturally treats any member who cannot be resolved to a Person (new join, Slack Connect external, bot, unlinked Telegram user) as a blocker because they are in the member set but not in the know set.
- Positive-only intersection (for example SpiceDB `.all()` over members) fails open when a member has no mapping and does not expose who blocked.
- Deny rules (never-share): evaluate as forbid-overrides-permit (Cedar's documented model) before audience logic. Engine support: SpiceDB `-`, OpenFGA `but not`, Cedar `forbid`, Zanzibar exclusion are all available; avoid wildcards on the right side of an exclusion or intersection in SpiceDB (documented CVE class). Note the privacy-sensitive caveat from CLAUDE.md: the free-text never-share list must be compiled into structured rules (topic/fact tags + recipient scope) and enforced in code, never pasted into the prompt for non-owners.
- Derived facts (summaries, inferences from F1 and F2) inherit audience = intersection of source audiences (information-flow labels; my inference, no source).
- Audience as snapshot vs live: "who learned it at the time" is an immutable set (person ids). Do NOT model as live `room#member` of the source room if you want "told in a room, later joiners of that room were not told." Pick deliberately; the live model gives later joiners access to history, which is the Slack default for public channels but is a surprise for DMs/groups.
- Slack Connect externals: treat external users as normal Persons with trust level `external`; they have no standing audience unless the fact was learned while they were present or the fact is public. Fail closed on unresolvable.
- Erasure: delete the fact row and its ACL rows in one transaction in Postgres. With an external engine you must also delete tuples (and any Watch-fed copies, caches, embeddings); that is a second system of record to keep consistent (inference).

---

## 4. Recommendation

### Takeaway
Phase 1 (single owner): plain Postgres tables plus a materialised audience table; no authorization service. Phase 2 (multi-tenant SaaS): keep Postgres as the retrieval-time enforcement point (tenant_id + RLS + the same ACL tables); add OpenFGA or SpiceDB only if/when relationship graphs become large, shared across services, or need customer-authored policy, and then feed it into the Postgres tables rather than querying it per candidate row.

### Reasoning (documented facts + inference)
- Retrieval needs the ACL predicate inside the vector query (pgvector post-filter shrinks results; ListObjects is capped e.g. SpiceDB 1000 default, OpenFGA ListObjects cost varies). A co-located predicate in the same SQL is the only option that preserves top-k.
- Single owner scale (thousands of facts, tens of rooms, membership depth 1-2): a join of `fact_audience` and `room_member` with an EXCEPT is microseconds-to-low-milliseconds. Adding a network service adds latency, a second source of truth for erasure, and ops burden for no modelling gain.
- A dedicated engine pays off when: (1) deep/wide graphs (nested groups, orgs, folders) where recursive CTEs become the bottleneck (Zanzibar needed Leopard denormalisation for deep nesting); (2) many services need one authorization answer (Zanzibar's original motivation); (3) tenants author their own policies and need an audit/explain plane; (4) you need cross-service snapshot consistency tokens (Zookie/ZedToken). None applies in phase 1.
- Engine choice when needed: SpiceDB has the most explicit consistency model and `.all()`; OpenFGA has the more permissive operator set (`but not`, CEL conditions), a store-per-tenant primitive and a Postgres backend. Both Apache-2.0. Avoid Permify (AGPL, ownership change, weak load test). Cedar/Cerbos suit attribute policies (for example "never share health topics with colleagues") better than relationship graphs; they can sit beside tuples for the owner rules.
- Whichever engine is used, keep the decision (allow / ask / refuse / public) in application code; engines return booleans or sets, not the four outcomes.

### Phase 1 minimal Postgres schema (sketch, inference)

```sql
-- all tables carry tenant_id, agent_id as in CLAUDE.md
person(id, tenant_id, kind /* owner|manager|colleague|external|bot */, ...)
room(id, tenant_id, channel, external_ref, kind /* dm|channel|thread|group */)
room_member(room_id, person_id NULL, platform_user_id, joined_at, left_at)   -- current = left_at IS NULL;
                                                                              -- person_id NULL for unresolved => blocker
fact(id, tenant_id, text, topic_id, public_version_id NULL, learned_in_room_id, learned_at, erased_at)
fact_audience(fact_id, person_id, PRIMARY KEY(fact_id, person_id))            -- snapshot at learn time; immutable
fact_public(fact_id)                                                           -- explicit public flag
topic(id, tenant_id, sealed bool)                                              -- sealed = owner-only, never disclose
never_share(tenant_id, topic_id NULL, fact_id NULL, person_id NULL, room_id NULL, effect 'forbid')

-- blockers for (room R, fact F)
SELECT m.platform_user_id, m.person_id
FROM room_member m
WHERE m.room_id = :R AND m.left_at IS NULL
  AND NOT (m.person_id IS NOT NULL AND (
        EXISTS (SELECT 1 FROM fact_audience a WHERE a.fact_id=:F AND a.person_id=m.person_id)
        OR EXISTS (SELECT 1 FROM fact_public p WHERE p.fact_id=:F)));
-- + forbid check separately
-- retrieval-time pre-filter: join candidate facts to the precomputed rooms/members the same way,
-- or maintain room_fact_ok(room_id, fact_id, membership_epoch) as a cache invalidated on
-- member change (rooms are few, so recompute per room on change).
```

Cache invalidation (inference): stamp `room.membership_epoch` (incremented on every join/leave, including Slack Connect events) and re-check at send time, not only at retrieval. This is the Zookie idea reduced to one DB: the epoch plays the part of the consistency token.

### Equivalent engine schemas (for phase 2 / evaluation; not run, syntax from docs; treat as untested drafts)

SpiceDB:
```
definition person {}
definition topic {
    relation barred: person            // never share this topic with these people
    permission is_barred = barred
}
definition room {
    relation member: person
    relation external_member: person   // Slack Connect
    permission participant = member + external_member
}
definition fact {
    relation audience: person          // snapshot written at learn time (expanded from room members)
    relation public: person:*
    relation about: topic
    relation barred: person
    permission may_know = (audience + public) - barred - about->is_barred
}
```
Room-level check is done in code: for each p in room.participant (LookupSubjects/ReadRelationships) run CheckPermission(fact:F, may_know, person:p). `public: person:*` is on the left of the exclusion, which avoids the wildcard-in-exclusion CVE pattern. Sealed (owner only) topics should be a code-level hard rule, not a wildcard exclusion.

OpenFGA:
```
model
  schema 1.1
type person
type topic
  relations
    define barred: [person]
type room
  relations
    define member: [person]
    define external_member: [person]
    define participant: member or external_member
type fact
  relations
    define audience: [person]
    define public: [person:*]
    define about: [topic]
    define barred: [person]
    define may_know: (audience or public) but not barred but not barred from about
```
OpenFGA note: `barred from about` is a tuple-to-userset; the DSL form is `barred from about` where `about` is the tupleset and `barred` a relation on `topic`. Confirm with the OpenFGA playground; I did not run it.

### Decision procedure (four outcomes) (inference)

Inputs: room R, fact F, current members M = members_now(R), owner rules.

0. If F erased or R unreachable: refuse (F treated as nonexistent).
1. Hard rules first (forbid beats everything, Cedar-style): if F or its topic is sealed, or any never_share rule matches (F, topic, any member of M, room R): go to step 4 (cannot allow, cannot ask).
2. Compute blockers = M minus know-set(F), including unresolved/external/bot members. If blockers is empty: ALLOW (serve F as is).
3. Blockers non-empty and no forbid: if F has a public version and a re-run of 1-2 on the public version passes: SERVE PUBLIC VERSION. Else if the owner can be asked in a private channel and the request is not hard-denied: ASK OWNER (prompt lists blockers and the specific fact, sent only to the owner; reply to the room is a neutral non-leaking deflection, with no hint a private fact exists). Owner approval creates a scoped one-time grant (a row in fact_audience for these members, recorded with who approved and when) rather than flipping the fact public.
4. Otherwise REFUSE (neutral reply; log reason internally). A forbid hit may still serve the public version if that version is itself clean.

Evaluate again on every membership change (join event) before any scheduled message; membership can change between retrieval and send.

### Test scenarios
1. Owner DM, fact learned in owner DM, ask in same DM: allow.
2. Fact learned in private DM with Alice; asked in a 3-person group of owner, Alice, Bob: blocker Bob; no public version; ask owner.
3. Same, but fact has public version: serve public version, never the original text.
4. Same room, Bob leaves, then asked: blockers empty; allow. Reverse: Bob joins between retrieval and send: re-check at send, blocked.
5. Slack Connect external user joins the channel: blocker; fact learned in the internal channel is not disclosed.
6. Unresolved Telegram member (no Person mapping): blocker (fail closed).
7. Bot or logging member in the room: blocker unless explicitly granted.
8. Never-share topic ("salary") with colleague member present: refuse even if colleague is in the audience (forbid beats audience).
9. Never-share rule scoped to one person; that person not in the room: allow.
10. Free-text never-share list: assert it never appears in prompt for a non-owner sender (regression for the top-priority leak in CLAUDE.md).
11. Owner declines the ask: refuse, and no grant row created; repeat ask in same room does not re-prompt endlessly (rate/limit).
12. Owner approves: grant is limited to the current member set; a later joiner is a blocker again.
13. Derived fact from F1 (audience {A,B}) and F2 (audience {B,C}): audience {B}.
14. Erasure: delete F; verify retrieval, public version, grants, embeddings, cache rows all gone; ask returns nothing.
15. Vector retrieval: 1,000 facts, 5% allowed for the room; top-k results contain k allowed items (pre-filter), not fewer (guards against post-filter shrinkage).
16. Membership removal of a source-room member: they no longer receive F in a new room unless in the snapshot audience (document the chosen semantics).
17. Race: membership epoch bump between plan and send forces re-evaluation.
18. Multi-tenant: tenant A fact never visible via tenant B room even with the same person platform id (RLS test).
19. Public wildcard plus exclusion (engine phase): `barred` person excluded from a public fact. Run the same case through LookupSubjects/ListObjects and Check; results must agree (guards the CVE class).
20. Revocation: remove person from audience, immediately Check/retrieve: denied (no stale cache; for engine phase assert with fully_consistent or ZedToken).

### Inferences summary
- Phase 1: Postgres only. Phase 2: Postgres remains the enforcement point; an engine, if added, is the source of truth for the relationship graph and is synced into Postgres ACL tables (Zanzibar-paper Expand/Watch-to-index pattern), with the four-outcome decision still in app code.
- Migration trigger checklist for adding an engine: tenant-authored policies, membership depth over 3 or groups over about 10^4 members, more than one service needing decisions, need for audit/explain UI, or recursive CTE latency exceeds the retrieval budget.

### Gaps
- No source documents a multi-party "all room members" disclosure model; this is original design and should be reviewed.
- OpenFGA DSL for the topic arrow exclusion and SpiceDB schema were not executed; run them in the playgrounds before adopting.
- CNCF status of OpenFGA and licenses of Cedar/OPA/Cerbos are from memory.
- Real latency of Postgres ACL joins at our scale is an estimate, not measured.
