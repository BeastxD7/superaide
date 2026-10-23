# Service architecture and reliability for a multi-channel agent memory pipeline (Superaide)

Date: 2026-10-23. Labels: DOCUMENTED = stated on a page I fetched; INFERRED = my reasoning; OPINION = source's view.

## Sources read (what I actually read; WebFetch returns model-summarised page text, not raw HTML)
- Slack Events API (https://docs.slack.dev/apis/events-api/): read fully via two queries. Slack Socket Mode (https://docs.slack.dev/apis/events-api/using-socket-mode): read.
- Telegram Bot API (https://core.telegram.org/bots/api): ONLY first 100k of 581k chars (getUpdates, setWebhook covered). Remainder unread.
- Fowler MonolithFirst (https://martinfowler.com/bliki/MonolithFirst.html): read. Shopify (https://shopify.engineering/shopify-monolith): read. Segment/Twilio "Goodbye Microservices" (https://www.twilio.com/en-us/blog/developers/best-practices/goodbye-microservices/): read.
- Prime Video: the primary post (primevideotech.com) redirected away and was NOT read. I only have secondary coverage (DevClass search snippet: https://devclass.com/2023/05/05/reduce-costs-by-90-by-moving-from-microservices-to-monolith-amazon-internal-case-study-raises-eyebrows/). Treat numbers as reported, not verified.
- microservices.io outbox (https://microservices.io/patterns/data/transactional-outbox.html): read. PostgreSQL 17 SELECT docs (https://www.postgresql.org/docs/17/sql-select.html): read the locking clause.
- DBOS architecture (https://docs.dbos.dev/architecture): read. Inngest waitForEvent + execution model (https://www.inngest.com/docs/reference/typescript/functions/step-wait-for-event, https://www.inngest.com/docs/learn/how-functions-are-executed): read. Restate (https://docs.restate.dev/concepts/durable_execution): read. Hatchet home (https://docs.hatchet.run/home): landing page only. Temporal (https://docs.temporal.io/evaluate/development-production-features/core-application, https://docs.temporal.io/self-hosted-guide): thin; pages lacked detail.
- NATS JetStream concepts (https://docs.nats.io/nats-concepts/jetstream): read, no dedupe-window detail. Redis Streams (https://redis.io/docs/latest/develop/data-types/streams/): first 100k of 277k chars; consumer-group/persistence prose NOT read. SQS FIFO exactly-once (https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/FIFO-queues-exactly-once-processing.html): read, partial (no group-ID/throughput text returned). Kafka docs: fetch failed (redirect), NOT read; RabbitMQ not fetched. DORA 2023 page (https://dora.dev/research/2023/dora-report/): no microservices statement found. OpenTelemetry messaging spans (https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/): read first 100k of 107k.
- Not researched (no fetch): Fly.io/Render/Railway specifics, Rust/Go benefit evidence, managed agent-memory services, SLO literature, Stripe-style idempotency. Treated as gaps.

## 1. Reliable ingestion of chat events

### Takeaway
Both Slack and Telegram deliver at-least-once-ish with weak ordering guarantees, so the durable design is: verify, write to an inbox table with a unique platform key, ack, then process asynchronously with per-conversation ordering and idempotent side effects. A Postgres inbox plus the existing jobs table covers this; no broker is needed at current scale.

### Cited Findings
- Slack: app must respond 2xx "within three seconds"; slower counts as failed delivery (`http_timeout`). — [Slack Events API](https://docs.slack.dev/apis/events-api/)
- Slack retries three times: nearly immediately, after 1 minute, after 5 minutes; headers `x-slack-retry-num` and `x-slack-retry-reason`. — same
- `event_id` is "globally unique across all workspaces"; the page gives NO dedupe guidance and does NOT state at-least-once or ordering (it says "best-effort system, and event delivery could be delayed during incidents"). — same. (Ordering non-guarantee is therefore an absence of promise, not an explicit statement.)
- Slack rate cap: 30,000 deliveries per workspace per app per 60 min, then `app_rate_limited` events; events >2 hours late not delivered by default (delayed-events feature changes this). — same
- Slack auto-disables subscriptions if <5% succeed in 60 min (apps under 1,000 events/hr exempt). — same
- Socket Mode: must ack each message with `envelope_id`; connections refresh every few hours, up to 10 concurrent connections, warning ~10 s before disconnect; payloads may go to any connection; not allowed in the public Marketplace; events during toggling may be lost; retry details not specified. — [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)
- Telegram getUpdates: offset must be one greater than highest received update_id; update confirmed once getUpdates is called with higher offset; ids "increase sequentially"; stored at most 24 hours; getUpdates and webhook are mutually exclusive. — [Bot API](https://core.telegram.org/bots/api#getupdates)
- Telegram webhook: non-2xx is retried "reasonable amount of attempts" (count unspecified); `secret_token` sent in `X-Telegram-Bot-Api-Secret-Token`; max_connections 1-100 (default 40). — same
- Transactional outbox: write the outgoing message in the same DB transaction as business data, a relay publishes it; relay may publish duplicates so consumers must be idempotent (track processed ids); order preserved. — [microservices.io](https://microservices.io/patterns/data/transactional-outbox.html)
- Postgres: `SKIP LOCKED` "can be used to avoid lock contention with multiple consumers accessing a queue-like table", but gives "an inconsistent view of the data", not for general use. — [PG 17 docs](https://www.postgresql.org/docs/17/sql-select.html)
- OpenTelemetry: producer and consumer spans connect mainly by span links to a message creation context; messaging semconv still "Development" status. — [OTel](https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/)

### Inferences
- Because Superaide uses Socket Mode (acks over websocket, no 3 s HTTP deadline, no Marketplace need for a single owner), the 3 s and retry-header machinery matters mostly if/when it moves to HTTP Events for a hosted multi-tenant product (Socket Mode is Marketplace-barred, so a public product likely needs HTTP). Plan for HTTP ingestion eventually.
- Slack edit/delete-before-original: no source says the order; since ordering is not promised, handle it defensively: store events keyed by (channel, message ts), allow a `message_changed`/`deleted` to create a tombstone/placeholder that the later original reconciles with. INFERRED.
- Ack-after-durable-write: ack only after the inbox insert commits; a crash between receipt and commit then yields a Slack/Telegram redelivery instead of loss. For Telegram polling, advance offset only after the inbox commit (then the 24 h retention bounds outage tolerance).
- Telegram update_id is a per-bot sequence, usable as dedupe key (bot_id, update_id); Slack key (team_id, event_id), with fallback (channel, ts, type) because retries after app-side loss may be re-sent with the same event_id but edits create different ones. The fallback is INFERRED.

### Reliability design (INFERRED; mine, not from a source)
```
Slack WS/HTTP, Telegram poll/webhook, Web chat
   |  verify signature / secret_token (drop unverified)
   v
[adapter] --INSERT--> inbox (UNIQUE channel_kind, source_id, event_key)  ON CONFLICT DO NOTHING
   |  commit, THEN ack (envelope_id ack / HTTP 200 / advance offset)
   v
[dispatcher] claims rows: SELECT ... FOR UPDATE SKIP LOCKED, one in-flight per conversation_key
   v
[pipeline: identity -> audience gate -> model(budget guard) -> policy]
   |  same txn: write memory/audit rows + INSERT outbox(reply)  (idempotency_key = inbox.id + step)
   v
[sender] claims outbox rows in per-conversation order -> channel API (send, store provider message id)
   failures -> retry w/ backoff -> dead_letter after N; operator replay
```
Tables:
```sql
inbox(id bigserial pk, tenant_id, agent_id, channel text, source_event_key text, conversation_key text,
      payload jsonb, received_at timestamptz, status text, attempts int default 0, locked_until timestamptz,
      last_error text, trace_id text,
      UNIQUE(tenant_id, channel, source_event_key));
outbox(id bigserial pk, tenant_id, agent_id, inbox_id fk, conversation_key text, seq int,
       kind text, payload jsonb, idempotency_key text UNIQUE, status text, attempts int, next_attempt_at timestamptz,
       provider_message_id text, sent_at timestamptz);
dead_letter(id, source_table, source_id, reason, payload, failed_at, replayed_at);
conversation_cursor(conversation_key pk, last_inbox_id_done bigint, in_flight_inbox_id bigint, lease_until timestamptz);
```
Rules: (1) dedupe by unique key on insert; (2) per conversation, process in inbox.id order, one at a time (lease on conversation_cursor; different conversations parallel); (3) retries with exponential backoff and jitter, max attempts then dead_letter, never block other conversations; (4) side effects (send reply, run approved action) go through outbox with idempotency key; since channel APIs offer no exactly-once, a crash between send and marking sent can double-send: record provider ids and accept rare at-least-once on replies, or check for the reply before resending; (5) backpressure: cap queue depth per tenant, shed to a fixed "busy" reply, budget guard refuses before model call; (6) keep raw payloads for replay; (7) one-way coupling: replay never re-sends outbox unless flagged.

## 2. Queue / bus options

### Takeaway
At Superaide's volume (a person's chats, far below Slack's 30k/hour cap) the existing Postgres jobs table with leases and SKIP LOCKED is sufficient and has the unique advantage that inbox/outbox writes share a transaction with memory writes. A broker is justified only at a split trigger (below).

### Cited Findings
- JetStream: at-least-once; unacked messages are redelivered; consumers track position, can replay from start/sequence/time; the page does not mention exactly-once or a dedupe window. — [NATS](https://docs.nats.io/nats-concepts/jetstream)
- Redis Streams: IDs monotonically increasing; consumer groups with XACK/XPENDING/XAUTOCLAIM exist; persistence/delivery prose not read. — [Redis](https://redis.io/docs/latest/develop/data-types/streams/)
- SQS FIFO: sender-side dedupe within a 5-minute interval ("exactly-once processing" is titled, but the claim is limited to not introducing duplicates into the queue on send retries). — [SQS](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/FIFO-queues-exactly-once-processing.html)
- Segment's single shared queue had head-of-line blocking from one slow destination; their fix was isolation per destination, later consolidated. — [Twilio/Segment](https://www.twilio.com/en-us/blog/developers/best-practices/goodbye-microservices/)

### Inferences
- "Exactly-once" is end-to-end only with idempotent consumers; every option here is at-least-once at the consumer (outbox source says so explicitly). Kafka/Redpanda/RabbitMQ not read: I make no claims about them beyond general knowledge, which should be verified; their ops cost (cluster, partitions, rebalancing) is plainly greater than Postgres for one owner (INFERRED).
- Head-of-line lesson from Segment maps to per-conversation keys (a stuck conversation must not block others).
- Ranking for Superaide (INFERRED): Postgres inbox/jobs now; if a broker is needed later, a managed SQS FIFO (MessageGroupId=conversation) or NATS JetStream is lighter than Kafka; Redis Streams adds a second state store with its own persistence caveats.

### Gaps
- Kafka, RabbitMQ semantics and ops costs, Redis persistence guarantees, JetStream dedupe window and SQS FIFO throughput limits: not verified from primary docs.

## 3. Workflow engines (long-running tasks, waiting for owner approval)

### Takeaway
DBOS (library over Postgres, no extra server) fits best architecturally; Inngest/Restate/Hatchet are credible; Temporal is the heaviest. For approval waits, a plain state machine in Postgres (pending_approval row plus a timer job) is enough today and migrating to DBOS later is cheap.

### Cited Findings
- DBOS: open-source library, "no separate orchestration server and no infrastructure required besides Postgres"; checkpoints step outputs and recovers by re-running from the last completed step; workflows must be deterministic and steps idempotent; Postgres-backed queues with concurrency control; one benchmark >40K workflows/steps per second per Postgres; Conductor control plane "strongly recommended" for production. HITL/send-recv page not covered in what I read. — [DBOS](https://docs.dbos.dev/architecture)
- Inngest: `step.waitForEvent` pauses until a matching event or timeout, returns null on timeout, suits human-in-the-loop approval; steps are memoized and retried individually; self-hosts as a single binary with SQLite or Postgres; each step is an HTTP call to your app. — [waitForEvent](https://www.inngest.com/docs/reference/typescript/functions/step-wait-for-event), [execution](https://www.inngest.com/docs/learn/how-functions-are-executed)
- Restate: journals each step and replays; server is a single Rust binary (cluster or single node); can suspend a handler awaiting external input; Virtual Objects allow one handler at a time per object (natural per-conversation serialisation); Python SDK exists; license not stated on the page. — [Restate](https://docs.restate.dev/concepts/durable_execution)
- Hatchet: built on PostgreSQL, MIT, Python/TS/Go/Ruby SDKs, hatchet-lite for small deployments; event-wait and concurrency keys not covered by the page. — [Hatchet](https://docs.hatchet.run/home)
- Temporal: self-hosting means choosing Docker/K8s/manual deploy, configuring visibility storage, TLS/auth, "safe, sequential server and schema upgrades"; dev server is a single binary. Inngest's docs claim Temporal needs several server components plus Cassandra or Postgres (vendor comparison, OPINION). — [Temporal self-hosted](https://docs.temporal.io/self-hosted-guide), [Inngest](https://www.inngest.com/docs/learn/how-functions-are-executed)

### Inferences
- Superaide's "approval wait" is a row with status and expiry; durable engines add value once multi-step agent tasks (research, follow-up reminders, multi-day retries) exceed what jobs+state-machine code handles cleanly.
- DBOS's determinism requirement conflicts with LLM calls inside workflow code unless each call is a step (it is; step output is checkpointed, which also avoids paying twice for a model call on replay: aligns with the budget guard).
- Pricing for all managed offerings: not researched.

## 4. Modular monolith vs services

### Takeaway
Evidence favours staying a modular monolith now; the only strong, evidence-backed reasons to extract are a security boundary for sensitive data and distinct scaling profiles, which Shopify itself used (credit-card vault, storefront rendering).

### Cited Findings
- Fowler: monolith first; boundaries are hard to get right early and cross-service refactors are harder; evidence is anecdotal ("I don't feel I have enough anecdotes yet"); a monolith converts only if built with strong modularity. OPINION. — [MonolithFirst](https://martinfowler.com/bliki/MonolithFirst.html)
- Shopify: modular monolith (37 components in the main monolith, Packwerk static checks on about a third); services only for "strong reasons": storefront rendering (read-heavy scale) and card vaulting (sensitive data). Strict rules added friction. — [Shopify](https://shopify.engineering/shopify-monolith)
- Segment: 140+ services consolidated; 3 engineers mostly keeping the system alive; fixed by one repo, one dependency version, a record/replay test tool (Traffic Recorder, suite runs in milliseconds); trade-offs: a crash in one destination takes down the whole service, weaker fault isolation. Segment says microservices still suit other parts. — [Twilio/Segment](https://www.twilio.com/en-us/blog/developers/best-practices/goodbye-microservices/)
- Prime Video (secondary only): audio/video monitoring service moved from Step Functions + S3 to a single process, reported ~90% infra cost cut; scope was one service, not all of Prime Video. — [DevClass](https://devclass.com/2023/05/05/reduce-costs-by-90-by-moving-from-microservices-to-monolith-amazon-internal-case-study-raises-eyebrows/) (verify against the original)
- DORA 2023 page: mentions loosely coupled teams but not microservices. I found no DORA evidence for microservices helping small teams. — [DORA](https://dora.dev/research/2023/dora-report/)

### Inferences
- Security-boundary argument is real: the Shopify vault precedent plus Superaide's rule "the model never writes identity/memory" suggest isolating the policy/disclosure engine (and credentials vault) behind a narrow internal API with its own DB role is the first extraction worth considering. Cheaper first step with ~80% of the benefit: separate Postgres roles and schemas, so adapters (Slack/Telegram processes) run with a DB role that can only INSERT into inbox and read outbox, never SELECT memory. Do this before any service split.
- Rust/Go/TypeScript rewrite of ingestion or the disclosure engine: no evidence found that it helps at this load; the bottleneck is model latency and cost. Not recommended.
- Managed agent-memory services: not researched; also a data-residency/disclosure-control risk since audience filtering must happen in your SQL.

## 5. Recommended topology and split triggers (INFERRED synthesis)

(1) Single owner now: one deployable (FastAPI modular monolith) + a separate worker process from the same image (consumes inbox/outbox/jobs via SKIP LOCKED) + Postgres. Adapters stay in-process or in the worker. Enforce module boundaries (import linter, Packwerk-like). DB roles per module. Docker Compose, single VM or Fly/Railway/Render (not researched; any works). OTel traces via trace_id stored in inbox and linked, plus the audit/disclosure log as an append-only table.

(2) Multi-tenant later, split when:
- Ingestion split: HTTP Events needed (Marketplace/many workspaces), or inbound spikes exceed worker capacity, or adapter crash/deploy must not drop connections -> separate stateless ingestion service writing to inbox only.
- Policy/disclosure/memory service: first paying third-party tenants, or compliance requires a compromised adapter be unable to read memory, or a separate team owns it. Put it behind a narrow API (decide(request) -> allowed memory ids/text).
- Model gateway: multiple tenants with separate budgets/keys, rate limiting across processes, or provider failover. Today BudgetGuard in-process is enough.
- Broker (SQS FIFO/NATS): inbox/jobs table contention (sustained thousands of jobs/sec, vacuum bloat), or multiple consumer services needing independent replay.
- Workflow engine (DBOS first, Temporal only if forced): more than a handful of multi-day agent flows with branching, or retry/timer code becoming bespoke.

## 6. Effort and risk (my estimates, INFERRED, one engineer)
- Inbox + dedupe + ack-after-commit for Slack/Telegram/web: 1-1.5 weeks; low risk.
- Outbox + idempotent sender + dead letter + operator replay: 1-1.5 weeks; medium (double-send edge cases).
- Per-conversation ordering lease: 0.5-1 week.
- DB role isolation for adapters/memory: 0.5 week.
- OTel traces + audit log: 1 week.
- Replay harness (below): 1-1.5 weeks.
- DBOS adoption for approval/follow-up flows: 1-2 weeks; medium (determinism rules).
- Extracting policy engine as a service: 3-5 weeks; adds deploy, auth, versioning, latency; only at trigger.

## 7. Offline reliability experiments
1. Record real Slack/Telegram payloads (Segment-style record/replay) as fixtures; replay the log into a fresh DB and assert identical memory/audit state (determinism with FakeLLM).
2. Duplicate injection: replay each event 1-4x, and with Slack retry headers; assert one reply and one memory write.
3. Reordering: permute events within a conversation (edit before original, delete before original) and across conversations; assert convergent final state and no cross-conversation blocking.
4. Crash injection: kill worker after each step (after inbox commit, after model call, before/after outbox send); restart; assert no loss, bounded duplicates (<=1 reply, flagged).
5. Poison message: failing handler goes to dead_letter after N attempts without stalling others; replay after fix.
6. Backpressure/budget: flood 10x rate; assert queue cap, BudgetGuard refusals, no overspend.
7. Disclosure property test: random audience/identity/message combos; assert out-of-audience memory never appears in prompt (checks the adapters' DB role cannot read memory).
8. Telegram offset: crash between fetch and commit; assert redelivery and dedupe.

## Gaps (summary)
Kafka/RabbitMQ/Redis persistence/JetStream dedupe/SQS throughput; Prime Video primary text; Telegram remainder of API; hosting platform costs; managed agent-memory services; SLO targets (my suggestion only: reply latency p95 under ~10 s excluding model, zero lost verified messages, duplicate replies near zero); Rust/Go evidence; DBOS human-in-the-loop API page; Temporal and Hatchet details beyond landing pages; no microservice-for-small-teams empirical study found.
