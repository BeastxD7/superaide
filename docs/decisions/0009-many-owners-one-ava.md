# 0009: Many owners, one Ava product

Status: accepted (2026-10-22). Built so far: T0 (instances), T1 (RLS and the app's role), T2 (accounts and
request scope), T3 (credentials, freeze and runtime per instance), T4 (each owner's own Telegram bot), T5 (jobs),
T6 (budget), T7 (an automatic per-instance filter on every ORM query in scope, identities and conversations per
instance), T8 (operator console: metadata by default, break-glass with a reason recorded in the owner's own log,
which they read at /app/access-log; operator accounts instead of ADMIN_TOKEN come later), T9 (email links), T10
"now" items and T12 (isolation e2e, marked expected-fail until many owners are switched on). T11 (a separate `worker` service with one
listener per bot by advisory lock), and Slack per instance (each owner's own Slack app, keyed "<team>/<app>"
like the identity layer's links, one Socket Mode connection per app in the owner's instance). Many owners is the default (decided 2026-10-22).

## Context

The owner corrected the product model: "Anyone can hire/buy Ava. It's the same agent product, but the
knowledge and data are specific to each owner. It's nothing like one agent, one owner." Superaide is a
marketplace. Many owners each hire their **own Ava instance**, and each instance has fully isolated
memory, people, reminders, connections, channels, approvals, audit and spend. Leo and Kai come later as
other agent products. He added: "we are building this for the world, globally."

Today's code is single-hire. Every row carries `tenant_id` and `agent_id` (ADR 0002), but both are
fixed constants in `config.py`, and almost no query filters on them. The column layout is ready for
tenancy; the queries, the runtime and the deploy are not.

### Facts checked in the dev DB (read-only, 2026-10-22)

- `person`: one distinct `tenant_id` and one distinct `agent_id`. One `agent_hire` (`ava`, active), one
  `user_account`, one `integration_secret`, 31 `memory_item`, 97 `event`, 53 `spend_ledger` rows. The
  migration below rewrites none of them.
- The app connects as role `agent` with `rolsuper = t` and `rolbypassrls = t`. **Row-Level Security
  would do nothing today**; the deploy has to change roles first (see section 4).

### Inventory: every single-tenant assumption

Paths are relative to `backend/src/superaide/`. "Leak" marks a bug that becomes a real cross-owner leak
the moment a second owner exists.

**Config, factory, main (one Agent object)**

| Where | Assumption |
|---|---|
| `config.py:13-14, 24-25` | `DEFAULT_TENANT_ID` / `DEFAULT_AGENT_ID` constants; `Settings.tenant_id` / `agent_id` are process-wide. |
| `config.py:28-39` | One Claude key, one model set, one `max_spend_usd` and one set of daily/monthly/bucket limits for the whole process. |
| `config.py:47-62` | One Telegram bot token, one Google OAuth client and refresh token, one Slack bot/app/team. |
| `db/models.py:38-43, 54-55, 323, 451-452` | Column defaults read `get_settings().tenant_id/agent_id`, so any insert without explicit ids lands in the dev tenant. Must fail closed instead. |
| `factory.py:46-53, 68-71` | `brain_status` / `build_llm` decide readiness and build one `BudgetGuard` from global settings. |
| `factory.py:74-89` | `build_messengers` builds one Telegram and one Slack messenger from global tokens. |
| `main.py:82-96` | `get_agent()` caches **one** `Agent`, rebuilt when the vault version (global) changes. |
| `main.py:98-105` | One `ChannelRunner` for the process; `apply_connections()` restarts it from one settings object. |
| `main.py:107-120` | `JobWorker` runs inside the API process. |
| `main.py:162-198` | Telegram and Slack webhooks route every update to the one agent. |
| `cli.py:27-150` | `telegram-poll`, `slack-socket`, `spend`, `freeze`, `add-manager` all act on the single tenant. |

**Pipeline**

| Where | Assumption |
|---|---|
| `pipeline.py:93-96` | Resolves identity and context before knowing which instance the message is for. |
| `pipeline.py:110` | `is_frozen(s)` reads the single agent's switch. |
| `pipeline.py:114-128` | Telegram `/start <code>` redeems any invite in the DB, with no check that it belongs to this bot's instance. **Leak** (an invite made for owner A redeemed via owner B's bot links to A's Person). |
| `pipeline.py:170-175` | `_is_owner` compares with `accounts.owner(s)`: **the** owner. |
| `pipeline.py:187-191` | Messenger picked by `ctx.provider` alone: with two bots, a reply can go out through the wrong owner's bot. |
| `pipeline.py:284` | Hard-coded "US$… spending cap" text from the global cap. |

**Identity and linking**

| Where | Assumption |
|---|---|
| `db/models.py:81` | `uq_identity_key (tenant_id, provider, tenant_scope, stable_key)`: one Telegram user can exist once per tenant, not once per instance. The same human talking to two owners' Avas must be two identities. |
| `identity/resolver.py:36-43` | `find_identity` has no instance filter. **Leak** (A's contact resolves as a known Person inside B's Ava). |
| `identity/linking.py:49-62` | `trusted_identities`, `find_person_by_name` search every Person by display name. **Leak** (a stranger to B can trigger a code to someone in A's people). |
| `identity/linking.py:82-130, 154-215` | Codes are delivered through the one `messengers` dict. |
| `db/models.py:343` | `UserAccount.person_id` is unique: one Person per account. With per-instance People, an account maps to a Person **per instance**. |
| `app/accounts.py:78-80` | The web identity is looked up by `stable_key = person.id` without instance; fine once Person is per instance, but must carry `agent_id`. |
| `app/accounts.py:149-161` | `redeem_invite` by code hash only. **Leak**, as above. |

**Audience gate, memory, recall**

| Where | Assumption |
|---|---|
| `context/audience.py:54-71` | `memory_filter` has no instance predicate. `public` and `org` memories match for any audience. **Leak** (B's colleague sees A's `org` notes). |
| `context/audience.py:80-86` | `visible_memories` unscoped. **Leak**. |
| `context/recall.py:122-190` | `recall_memories` / `recall_messages` build on `memory_filter` / `_message_gate` with no instance predicate. **Leak**. |
| `memory/store.py:22-48`, `db/models.py:125` | `get_or_create_context` and `uq_context_key` keyed on tenant, not instance: two owners' bots in the same Telegram group share one Context row. |
| `memory/store.py:200-230` | `remember` dedupes against every instance's memories. |
| `db/models.py:148, 174`, `context/recall.py:31` | Full-text search fixed to `'english'` (see Global). |

**Knowledge**

| Where | Assumption |
|---|---|
| `knowledge/profile.py:17, 48-56` | `owner_brief` calls `accounts.owner(s)`: the one owner. |
| `knowledge/store.py:84-90` | `preferences_for` by person id; fine once Person is per instance. |
| `knowledge/store.py:93-125` | `add_contact` creates contact People with tenant defaults. |
| `knowledge/learn.py:72-75` | Builds a fresh LLM from global `runtime_settings()`: one key and one budget for every owner's learning. |
| `knowledge/ingest.py:35-148` | Takes `owner_id` from the route; needs the instance's LLM and budget. |

**Intentions, jobs, proactive**

| Where | Assumption |
|---|---|
| `jobs/queue.py:62`, `db/models.py:480` | `dedupe_key` unique across the whole table. |
| `jobs/queue.py:76-94` | `_claim` takes the 20 oldest due jobs globally: one busy owner starves the rest. |
| `jobs/queue.py:101-131` | Handlers run in an unscoped session. |
| `jobs/worker.py:25-32`, `proactive/digest.py:207-212` | `ensure_scheduled` schedules the digest and meeting scan for **the** owner only. |
| `proactive/digest.py:153-154` | Digest notes via `memory_filter`, unscoped. **Leak**. |
| `proactive/digest.py:172` | Counts **all** pending approvals in the DB. **Leak** (count only, but still). |
| `proactive/notify.py:49-61` | `messenger_for` builds Telegram/Slack messengers from global `runtime_settings()`. Reminders for owner B would go out through owner A's bot. |
| `intentions/service.py:54-55` | `owner_zone` falls back to `"UTC"`; fine. |
| `skills/calendar.py:23-59` | Google tokens from global `runtime_settings()`: every owner's calendar would be the dev owner's calendar. **Leak**. |

**Skills registry**: `skills/registry.py:36-37` `_rows` reads every `installed_skill`; `db/models.py:514`
already keys on `agent_id`, so only the reads need scoping.

**Vault**

| Where | Assumption |
|---|---|
| `db/models.py:403-420` | `IntegrationSecret` is `TenantScoped` (no `agent_id`); unique per `(tenant_id, provider, name)`. |
| `vault/store.py:37-38` | AES-GCM associated data is `provider/name` only, so a ciphertext copied between instances still decrypts. |
| `vault/store.py:49, 65, 74, 80-81, 84-90` | `save`, `remove`, `stored`, `version` read and write every row. **Leak**. |
| `vault/store.py:92-101` | `runtime_settings()` overlays the vault onto the process `Settings`: credentials are process state. |
| `vault/crypto.py:93-107` | One master key for all secrets (acceptable now; see section 6 for per-tenant keys later). |

**Channels**: `channels/runner.py:21-75` holds one Telegram poller and one Slack Socket Mode handler;
`channels/telegram.py:44` sets `tenant_scope = ""` and `poll_forever` uses `getUpdates`, which allows
one consumer per bot; `channels/slack.py:39-67` takes one `our_team_id`.

**Budget**: `brain/budget.py:25, 100-158` sums the whole ledger and serialises **every** reservation on
one advisory lock (`_LOCK_KEY = 784_221_001`). `db/models.py:303` owner prices are already unique per
`agent_id`.

**Policy**: `policy/killswitch.py:15-50` per agent from settings; no platform-wide or per-account freeze.
`channels/health.py` (keys `channel_health:<provider>`) and `proactive/notify.py` (`channel_failure_noted:<provider>`)
store channel health and the failed-delivery note's rate limit in `agent_state` under the settings agent, the same
way; they need the instance's `agent_id` passed in once credentials are per instance.
`policy/actions.py:53` approvals by `relation == "manager"`, which is global on `Person`.
`app/connections.py:231-254` keeps the Google OAuth pending state in `agent_state` under the settings
agent, and the callback has no session, so the state must name the instance.

**Admin console**: `api/admin.py:36-48` is one bearer `ADMIN_TOKEN`; every endpoint (`/conversations`,
`/memories`, `/approvals`, `/spend`, freeze, simulator) reads across everything.

**Auth**: `db/models.py:337` and `auth/service.py:97` make email unique per tenant; accounts must be
platform-wide. `email_verified_at` (`db/models.py:347`) is never set; sign-up has no verification.

**App routes**: `app/routes.py:73-79` `owner_person_id` finds *a* hire and returns the account's single
Person; `app/routes.py:133-145` reports `hired` (whether Ava is taken); `app/routes.py:156-174` refuses
with 409 "already works for someone else"; `app/accounts.py:40-70` `AlreadyHired`;
`db/models.py:388-391` `uq_agent_hire_active (tenant_id, agent_slug)` enforces one Ava per tenant;
`app/routes.py:343` looks up the web context by external id only.

**Frontend**: `features/marketplace/Marketplace.tsx:91, 275-287` and `features/marketplace/Profile.tsx:49-89`
("Ava is already working for someone", "Already hired"); `lib/api.ts:19` `hired?: boolean`;
`lib/session-context.tsx:9-66` assumes at most one hired agent.

**E2E**: `e2e/src/db.ts:22-28` truncates to "nobody has hired Ava yet"; `e2e/src/fixtures.ts:55-100`
one `owner` fixture. There is no second owner anywhere in the suite.

**Deploy**: `deploy/README.md:113-115` ("the first account to hire Ava becomes the owner") and
`deploy/bootstrap.sh:143`; `docker-compose.prod.yml:40-75` runs one `api` service that also runs the job
worker and the channel listeners; the database role is a superuser.

## Research

### Isolation patterns for Postgres SaaS

- **Shared schema + tenant column + RLS** is the standard pattern for many small tenants (AWS SaaS
  Lens, Citus, Supabase, Nile). Schema-per-tenant and database-per-tenant isolate better but cost
  migrations and connections per tenant; they suit tens of large tenants, not thousands of individuals.
- **RLS mechanics that matter**: policies do not apply to superusers or roles with `BYPASSRLS`, and do
  not apply to the table owner unless `FORCE ROW LEVEL SECURITY` is set. So the app needs its own
  non-owner, non-superuser role, and migrations run as a separate owner role. The per-request value goes
  in a custom setting with `set_config('app.agent_id', $1, true)`; the `true` makes it transaction-local,
  which is safe with connection pools including PgBouncer transaction mode. A policy written as
  `agent_id = nullif(current_setting('app.agent_id', true), '')::uuid` returns no rows when the setting is
  missing, so forgetting it fails closed. Policies need both `USING` and `WITH CHECK`.
- **SQLAlchemy**: set the value in a `Session` `after_begin` event (runs on every transaction, including
  ones opened by `begin_nested`'s parent), reading a `contextvars.ContextVar`. Never on the engine
  `connect` event: pooled connections would keep one tenant's value.
- **Performance**: `current_setting` is stable, so the planner treats the policy as a constant filter;
  every hot index should lead with or include `agent_id`. pgvector 0.8 iterative index scans keep HNSW
  usable under a selective filter; at our sizes a btree on `agent_id` plus exact search is fine.

### How agent platforms isolate per-customer memory

- **Mem0** scopes every memory call by `user_id` / `agent_id` / `run_id` (and `org_id`/`project_id` in
  the hosted platform); scoping is an argument the caller must pass, not enforced by the store.
- **Letta** gives each agent its own memory blocks and archival store, keyed by agent id, with
  identities and organisations above that.
- **OpenClaw** isolates multiple agents by giving each its own workspace directory, session store and
  credentials, and routes inbound channel messages to an agent by binding (account, channel, peer).
- The common shape: the **agent instance** is the memory boundary, the customer/org is the billing and
  admin boundary, and inbound routing is done by the channel binding before anything else. None of them
  rely on the model to keep tenants apart. We go one step further with RLS in the database.

### Per-tenant fairness in a Postgres queue

`FOR UPDATE SKIP LOCKED` cannot be combined with window functions or `DISTINCT ON` in one statement.
The usual fair claim is two steps in one transaction: a CTE picks up to *k* due job ids per instance
with `row_number() OVER (PARTITION BY agent_id ORDER BY run_at)`, then `SELECT … WHERE id = ANY(...)
FOR UPDATE SKIP LOCKED`. Add a per-instance cap on jobs running at once. This is the same idea as
Oban's and Graphile Worker's per-queue concurrency, applied per tenant.

### Channels

- **Telegram**: one user has exactly one private chat with a given bot. A single shared platform bot
  therefore **cannot** tell which owner a stranger means, unless every first message carries a start
  payload (`t.me/bot?start=<payload>`, up to 64 characters of `A-Za-z0-9_-`), and even then later
  messages are ambiguous for someone who knows two owners. Options:
  1. **Bring your own bot** (today's flow: the owner pastes a BotFather token). One bot per instance;
     routing is by bot. Works, but asks the owner to use BotFather.
  2. **Managed bots** (Bot API 9.6, April 2026): a manager bot with management mode enabled sends the
     owner a `t.me/newbot/...` link; once they confirm, the manager receives a `managed_bot` update and
     calls `getManagedBotToken` to get the child bot's token. One tap gives each owner their own bot
     (e.g. `@ShashankAvaBot`), with routing by bot and no BotFather. *To confirm against the official
     docs before building: limits per owner, and who owns the bot if the owner leaves.*
  3. **Business connections** (Bot API 7.2, *from memory; to confirm*): an owner with Telegram Premium connects a bot to their
     personal account, and the bot answers messages people send to the **owner's own account**, each
     update tagged with a `business_connection_id` that maps to one instance. Excellent fit for a
     personal assistant, but Premium only.
  - Webhooks (`setWebhook` with a per-bot `secret_token`) replace `getUpdates` polling: polling allows
    one consumer per bot and does not scale to thousands of bots.
- **Slack**: a distributed app uses OAuth v2 and gets a bot token per workspace (`team_id`, and
  `enterprise_id` on Enterprise Grid). Socket Mode is not allowed for apps in the public Slack
  Marketplace (*from memory; to confirm*), so a distributed app must take Events over HTTP with the signing secret. Two owners in the
  same workspace would share one install and one bot user, which is ambiguous for third parties.
- **WhatsApp Business Platform**: from 15 January 2026 Meta's terms bar providers whose core product is
  a general-purpose AI assistant from the Business API. Ava is likely in scope, so WhatsApp is **not
  permitted** for now; revisit only with legal advice.
- **LINE Messaging API** (Japan, Taiwan, Thailand): one official account (channel) per bot, the same
  per-instance pattern as Telegram option 1. **WeChat** Official Accounts need a mainland Chinese entity
  and PIPL compliance: out of scope.

### Law (for the Global section)

- **EU AI Act Art. 50** transparency obligations have applied since **2 August 2026**; the Digital
  Omnibus (Reg. (EU) 2026/1744) moved the high-risk deadlines but not Art. 50. People who interact with
  Ava must be told they are dealing with an AI unless it is obvious. Fines up to EUR 15m or 3%.
- **India DPDP Rules 2025**, notified 13-14 November 2025, phase in over 18 months; Data Fiduciary
  obligations apply from about **13 May 2027**. Notice and consent, rights of the Data Principal,
  breach notification to the Data Protection Board.
- **GDPR (EU/UK)**, **PDPA (Singapore)**, **CCPA/CPRA (California)**: rights of access, export,
  correction and deletion; processor terms; breach notification (PDPA: 3 calendar days after assessing
  a notifiable breach; GDPR: 72 hours). *Exact obligations per role to confirm with counsel.*

## Decision

### 1. The tenant boundary: the agent instance

Use the two columns every row already has:

- **`agent_id` = the agent instance** (one hire of Ava by one owner). This is the isolation boundary for
  every piece of data: People, identities, contexts, events, memories, documents, contacts,
  preferences, intentions, approvals, audit, spend, installed skills, credentials, channel endpoints,
  jobs and agent state. RLS keys on it.
- **`tenant_id` = the customer**: the billing and administration entity that owns one or more instances.
  Today it is 1:1 with the owner's account (a personal workspace). Later it can be a company with
  several members and several agents (Ava, Leo, Kai). It carries plan, billing currency, region and
  default locale.

New platform-level tables (no RLS on agent, RLS by tenant where relevant):

```
tenant          (id, kind personal|org, name, region, default_locale, default_timezone,
                 billing_currency, plan, status, created_at)
agent_instance  (id = agent_id, tenant_id, agent_slug, display_name, status active|paused|ended,
                 owner_user_id, region, locale, timezone, key_source owner|platform,
                 created_at, ended_at)
instance_member (agent_id, user_id, person_id, role owner|admin, created_at)   -- account -> Person per instance
channel_endpoint(id, agent_id, provider, kind own_bot|managed_bot|business_conn|slack_install|platform_bot,
                 external_key  -- Telegram bot id, Slack team_id[:enterprise_id], business_connection_id
                 , webhook_secret_hash, status, created_at)  UNIQUE (provider, external_key)
platform_state  (key, value)                                    -- platform freeze, platform spend guard
```

`agent_hire` becomes a view or a thin record pointing to `agent_instance` (`instance_id` column);
`uq_agent_hire_active` changes from `(tenant_id, agent_slug)` to `(owner_user_id, agent_slug)`: **one
active Ava per account**, not one Ava in the world (see open question 4).

Schema changes the inventory forces:

- `uq_identity_key` and `uq_context_key` move from `tenant_id` to `agent_id`. The same Telegram user is a
  separate `channel_identity` and `person` in each instance. **People are never linked across
  instances**: owner A's Ava must not learn that her contact Priya is also owner B's colleague.
- `UserAccount` becomes platform-wide: `uq_user_email` on `email_normalized` alone, `person_id` dropped
  in favour of `instance_member`. `UserSession` and `AuthEvent` stay platform-wide.
- `IntegrationSecret` gains `agent_id`; unique `(agent_id, provider, name)`.
- `Job.dedupe_key` unique becomes `(agent_id, dedupe_key)`.
- `Person.relation` stays on Person, which is now per instance, so "manager" means "manager of this
  instance". `instance_member` is the source of truth for who owns an instance; `relation = manager` is
  set from it.
- `db/models.py` defaults `_tenant()` / `_agent()` read the tenancy context (below) and **raise** when it
  is unset. `config.py` keeps `DEFAULT_*_ID` only for the migration and the CLI's `--instance` default
  in development.

### 2. Request to instance resolution

One module, `tenancy/context.py`, owns a `ContextVar[InstanceScope | None]` (`tenant_id`, `agent_id`,
`actor`) and a `use_instance(agent_id)` context manager. **Every** entry point sets it before touching
data:

| Entry point | How the instance is found |
|---|---|
| Web app (signed in) | URL carries the instance: `/w/{instance_slug}/...` in Next.js, `X-Superaide-Instance` (or path param) to the API. Dependency `current_instance(user, instance)` checks `instance_member` and returns `(agent_id, person_id)`. Replaces `owner_person_id`. An account with one instance is redirected to it. |
| Telegram webhook | `POST /webhooks/telegram/{endpoint_id}`; verify the endpoint's `secret_token` header, then `endpoint.agent_id`. Business updates: `business_connection_id` -> endpoint. |
| Slack Events | Verify signature with the app's signing secret, then `(team_id, enterprise_id, api_app_id)` -> endpoint -> instance. |
| Job | `job.agent_id` (the claim runs as the worker role; each job's handler runs inside `use_instance(job.agent_id)`). |
| Google OAuth callback | The one-time `state` row is stored with `agent_id`; the callback looks up the state, then scopes. |
| Operator console | Explicit instance chosen in the console, audited (section 9). |
| CLI | `--instance <id>`; required when more than one exists. |

Anything that happens before an instance is known (a bad webhook secret, an unknown `endpoint_id`, an
unroutable Slack team, a dropped unverified message as at `pipeline.py:88-91`) is audited in a
**platform-level** audit table without `agent_id` (like `auth_event`), never in an instance's log; with
fail-closed defaults such an insert into `audit_log` would raise. Platform-guard and operator actions
are audited there too (operator break-glass is additionally written to the instance's own log).

Everything after this point is unchanged in shape: the pipeline resolves identity, audience and policy
inside the instance. `accounts.owner(s)` becomes `instance_owner(s)`, reading the scoped
`instance_member` row.

### 3. Instance runtime instead of one Agent

`main.py`'s single cached `Agent` becomes an `InstanceRuntimes` registry: an LRU cache of
`InstanceRuntime(agent_id, config, llm, messengers, limits)` keyed by `agent_id`, rebuilt when that
instance's vault/provider version changes. `Settings` keeps only platform configuration (database,
cookies, platform keys, platform caps). Per-instance configuration (provider and model roles from ADR
0007, limits, locale, timezone, channels) is loaded from the instance's rows.

Messengers are resolved from the **endpoint the conversation came in on** (`context.endpoint_id`, a new
column), never from `ctx.provider` alone, and `notify_owner` resolves the owner's endpoint within the
instance. This closes "reply sent through the wrong owner's bot".

### 4. RLS: yes, as the second line of defence

App-level filters stay the first line (every query is written for one instance and the scope is set
explicitly). RLS makes a forgotten filter fail closed instead of leaking.

- **Roles**: `superaide_owner` owns the schema and runs Alembic; `superaide_app` (no superuser, no
  `BYPASSRLS`, not owner) is what the API and workers use; `superaide_ops` (`BYPASSRLS`, read-only
  grants) exists only for platform aggregates and break-glass, and is never used by request code.
- **Policies** on every table with `agent_id`:
  `ENABLE` + `FORCE ROW LEVEL SECURITY`;
  `USING (agent_id = nullif(current_setting('app.agent_id', true), '')::uuid)` and the same `WITH CHECK`.
  Tenant-level tables (`tenant`, `agent_instance`, `instance_member`) use `app.tenant_id` or are read
  through `SECURITY DEFINER` functions that take the authenticated user id.
- **Session hook**: `db/session.py` registers `after_begin` that runs
  `SELECT set_config('app.agent_id', :a, true), set_config('app.tenant_id', :t, true)` from the context
  variable (empty string when unset, which means zero rows).
- **The job claim** is a `SECURITY DEFINER` function `claim_jobs(limit, per_instance)` owned by
  `superaide_owner`, so the worker can see due jobs across instances without `BYPASSRLS`; it returns ids
  and kinds only.
- **Tests**: tests build the schema with `create_all` (CLAUDE.md), which creates no policies or roles.
  Decision: the tenancy tests run Alembic `upgrade head` on their own database (`agent_test_tenancy`)
  and connect as `superaide_app`; the rest of the suite keeps `create_all` plus a helper
  `apply_rls(engine)` that runs the same policy DDL as the migration (one source: a list in
  `db/rls.py` used by both). Required tests:
  1. Meta-test: every table with an `agent_id` column has `relrowsecurity` and `relforcerowsecurity`
     (catches new tables from ADR 0007/0008 and later).
  2. Two instances with memories, events, People, secrets, jobs and spend; with scope A, every model's
     `SELECT *` returns only A's rows; raw SQL without a scope returns zero rows; inserting a row with
     B's `agent_id` under scope A raises.
  3. The app role cannot `SET ROLE`, cannot `ALTER TABLE ... DISABLE ROW LEVEL SECURITY`, and has
     `rolbypassrls = false`.
  4. Pipeline-level: B's colleague asks B's Ava about A's `org` memory and gets nothing; A's Telegram
     invite code sent to B's bot is rejected.

### 5. The audience gate inside an instance: unchanged

`audience_for`, `memory_filter`, `recall` and `owner_brief` keep their semantics. They gain an explicit
`MemoryItem.agent_id == scope.agent_id` predicate (belt and braces with RLS), and that is all. The rules
"the model never writes identity, trust or memory tables" and "filter inside SQL" are unchanged.

### 6. Vault and credential scoping

- `integration_secret` per instance; associated data becomes `"{agent_id}/{provider}/{name}"`, so a
  ciphertext moved to another instance fails to decrypt. The one existing row is re-encrypted in the
  migration.
- **Platform credentials** (platform Claude key, manager bot token, Slack app client secret and signing
  secret, email provider key) live in `platform_secret` (no `agent_id`, only readable by the platform
  code path) or deploy secrets. Request code for an instance can never read them; the platform key is
  used only through the budget-guarded provider (section 7).
- Google: the platform registers one OAuth client (needs Google verification for Calendar scopes);
  each instance stores its own refresh token. The owner's own OAuth client stays as an option for
  self-hosting.
- Later: envelope encryption with a data key per tenant, wrapped by the master key (or a cloud KMS).
  Deleting a tenant's data key makes its secrets unreadable in old backups too (crypto-shredding).

### 7. Budget: per instance, per account, plus a platform guard

- The ledger gains `key_source` (`owner` | `platform`). Each reservation checks, in order:
  1. **Instance limits** (lifetime/monthly/daily/bucket, today's `Limits`), stored per instance.
  2. **Account/plan limit** across the account's instances when the platform key pays.
  3. **Platform guard** when the platform key pays: a single row in `platform_state`
     updated atomically: `UPDATE platform_spend SET reserved = reserved + :x WHERE reserved + :x <= cap
     RETURNING ...`, released or adjusted on settle. No global lock.
- The advisory lock becomes per instance: `pg_advisory_xact_lock(hashtext('spend:' || agent_id))`.
  Owners stop serialising behind each other.
- Who pays decides which limits count (updated 2026-10-22). The lifetime cap counts every call, whoever paid.
  The owner's day, month and bucket limits count only what their own key paid. Credit-paid calls answer
  to the credits allowance and the platform guard instead, and are always priced from the built-in
  tables, never an owner's price. So using up the credits never blocks the owner's own key, and the
  owner's limits never cut the credits short of the allowance the AI model page shows.
- Spend on the owner's own key is capped by the owner's own limits; the platform still applies rate
  limits (calls per minute per instance) so a runaway loop cannot hog workers.
- Billing hooks for later: a `usage_record` per settled entry (instance, tenant, provider, cost in USD,
  billable amount), so Stripe usage-based billing or invoices can be generated without touching the
  ledger.

### 8. Jobs and the worker

- Fair claim (two-step, per-instance quota, per-instance running cap) through `claim_jobs()`.
- Handlers run inside `use_instance(job.agent_id)` and get the instance runtime (LLM, messengers).
- `ensure_scheduled` loops over active instances (or, better, each instance schedules its own digest
  when its preferences change, and a nightly sweep repairs gaps).
- A separate **`worker`** service in `docker-compose.prod.yml` (`RUN_JOBS_IN_API=false`,
  `RUN_CHANNELS_IN_API=false` in the API). Workers scale horizontally.
- Channel listeners: webhooks need no listener. Anything that must poll (BYO Slack Socket Mode during
  the transition, dev Telegram polling) is owned per endpoint with `pg_try_advisory_lock(hashtext('poll:'
  || endpoint_id))`, so exactly one process polls each bot, and endpoints spread across workers.

### 9. Channel strategy

| Phase | Telegram | Slack |
|---|---|---|
| Now (with tenancy) | **Bring your own bot** per instance, as today, moved to webhooks with a per-endpoint secret. Routing by bot. | **Bring your own app** per instance (today's manifest + Socket Mode), owned by one worker via advisory lock. |
| Next | **Managed bots**: one platform manager bot creates each owner's own bot in one tap. Default for new owners. | **One distributed Slack app**, OAuth v2 + Events over HTTP. One instance per install (`team_id`); a second owner in the same workspace uses BYO app. |
| Later | **Business connections** for Premium owners (Ava answers on the owner's own account). A shared platform bot only for **owner-only** use (notifications, onboarding), routed by the owner's linked identity. | Enterprise Grid (`enterprise_id`). |

Strangers are never routed by guessing: a message that cannot be tied to one endpoint is dropped and
audited.

### 10. Policy and the kill switch

`is_frozen` becomes: instance frozen **or** tenant frozen **or** platform frozen (`platform_state`).
The owner freezes their own instance from the app; operators freeze an instance, a tenant or the
platform from the console. Approvals are per instance (RLS), decided only by that instance's members.

### 11. The operator console

The console becomes a **platform** console with operator accounts (role `operator` on `user_account`,
MFA later) instead of one shared `ADMIN_TOKEN`:

- Default views are **metadata only**: tenants, instances, status, spend, job health, channel health,
  errors. These come from `superaide_ops` aggregate functions, never from browsing rows.
- Viewing an instance's conversations or memories is **break-glass**: a reason is required, it is
  audited in the instance's own audit log (so the owner can see it), and later it needs the owner's
  support grant.
- The simulator works only on a chosen dev instance.

### 12. Migrating today's dev tenant (no data loss)

One Alembic revision (after ADR 0007/0008 revisions):

1. Create `tenant` row `DEFAULT_TENANT_ID` and `agent_instance` row `DEFAULT_AGENT_ID` from the active
   `agent_hire` (owner, slug, notice); `instance_member` from `user_account.person_id`.
2. Assert every scoped row already has these two ids (it does: one distinct value each); abort otherwise.
3. Add `agent_id` to `integration_secret` (= `DEFAULT_AGENT_ID`) and re-encrypt with the new associated
   data (the migration reads the master key, as the app does).
4. Swap unique constraints (`uq_identity_key`, `uq_context_key`, `uq_integration_secret_slot`,
   `job.dedupe_key`, `uq_user_email`, `uq_agent_hire_active`).
5. Create `channel_endpoint` rows for the existing Telegram bot (from the vault) and set
   `context.endpoint_id` for existing contexts.
6. Roles and RLS policies in a **separate** revision, so it can be applied after the deploy has switched
   the app to `superaide_app`. Downgrade drops policies and new tables only.

Backup first (the deploy's `backup.sh`), then run; a test runs the migration on a copy of a seeded DB
and checks row counts and that the dev owner still sees all 31 memories.

### 13. Impact on in-flight work

- **ADR 0007 providers**: `llm_provider`, `model_role` and `model_price` carry `agent_id` and are unique
  per instance (as `owner_model_price` already is). Add a provider kind **`platform`** (the hosted key,
  `key_source = platform`, never shown or exported). **0007 allows private-range `base_url` for
  ollama/vllm/custom: on the hosted platform that is an SSRF path into our own network, so private,
  loopback and link-local targets must be refused in hosted mode** and allowed only when
  `DEPLOY_MODE=self_hosted`. Vault slots `llm/<provider_id>/api_key` gain the instance in their
  associated data.
- **ADR 0008 cross-channel memory**: `owner_timeline` is keyed by the owner's Person, which is per
  instance, so continuity stays within the instance. Recall gains the `agent_id` predicate; the FTS
  indexes should become `(agent_id, tsvector)` composite or partial as data grows.
- **Reminders channel** (`intention.channel`): delivery resolves the instance's endpoint for that
  provider; "Telegram isn't linked" messages are per instance.
- **Deploy (DevOps)**: database roles in `deploy/postgres/` init, `DATABASE_URL` for the app role and a
  separate `MIGRATION_DATABASE_URL`, a `worker` service, webhook routes behind Caddy, and the README /
  bootstrap rewrite (section 15).

### 14. Threat model

| Threat | Mitigation | Proof |
|---|---|---|
| Cross-tenant read through a missing filter (`memory_filter`, `digest.py:172`, `vault.stored`, registry, admin) | Explicit scope + RLS fail-closed | RLS meta-test and two-instance read tests |
| IDOR by id in a URL (`/memories/{id}/forget`, documents, intentions) | RLS hides other instances' rows, so lookups 404 | API tests with B's session against A's ids |
| Cross-wired channels: reply or reminder via another owner's bot; invite redeemed through another bot | Messenger from `context.endpoint_id`; invites and link challenges scoped by instance | Pipeline tests with two endpoints |
| Identity confusion: one Telegram user in two instances | Identity and Person per instance; no cross-instance linking | Resolver test |
| Prompt injection via another tenant's content | Retrieval cannot reach other instances (RLS); shared caches (prompt cache, web fetch cache, Google token cache) never mix tenant content into another tenant's prompt; the charter is the only shared prompt text | Review + recall tests |
| Platform-key abuse / noisy neighbour | Per-instance spend and rate limits, platform guard, fair job claim, per-instance running cap, upload size caps | Budget and queue tests |
| Account enumeration | Hire no longer reveals "taken"; login and sign-up keep their uniform errors | Existing auth tests |
| SSRF through provider `base_url` | Hosted mode refuses private targets (13) | URL validator table |
| Operator overreach | Metadata-only console, break-glass audit visible to the owner | Console tests |
| Deletion that leaves data in logs or backups | Content never logged; per-tenant data keys later; backup retention stated in the privacy notice | Review |

### 15. Hosted and self-hosted

The VPS deploy (DevOps, `deploy/`) is genuinely single-owner, and many people will run it that way. Both
modes stay, behind `DEPLOY_MODE = hosted | self_hosted`:

- `self_hosted`: one tenant, the operator is the owner, BYO credentials only, private `base_url`
  allowed. The first verified account becomes the owner by invitation code printed by `bootstrap.sh`,
  not by "whoever hires first" (that race is a takeover risk on a public URL).
- `hosted`: sign-up open, many instances, platform credentials, managed bots.

`deploy/README.md` section 5 and the `bootstrap.sh` closing message are rewritten to: "open the URL,
sign up with the email you gave bootstrap, enter the one-time owner code it printed, verify your email,
then hire Ava".

## Global

Owners anywhere, with their own language, time zone, currency and law.

| Area | Needed now (with tenancy) | Later |
|---|---|---|
| Time zone | No hard-coded zone. `UserAccount.timezone` and `OwnerPreference.timezone` default to `Asia/Singapore` (`db/models.py:346, 248`): default from the browser's `Intl.DateTimeFormat().resolvedOptions().timeZone` at sign-up, stored per instance. All storage stays UTC. | Travel detection (ask when the phone's zone changes). |
| Language of chat | Ava replies in the language she is addressed in (charter rule). Fixed code strings (`pipeline.py` stranger reply, "Saved to your notebook", reminder confirmations, digest text) move to a message catalog keyed by the instance locale. | Full UI translation (Next.js i18n), RTL layouts for Arabic, Hebrew, Urdu. |
| Search in other languages | `'english'` FTS (`db/models.py:148, 174`, `recall.py:31`) and the en-GB/en-US lexicon only work for English. Add a `'simple'` FTS arm (no stemming, works for any script) next to the English one; store `lang` per memory. | Per-language configs where Postgres has them; multilingual embeddings (pgvector) as the language-neutral arm (ADR 0008 plans it). |
| Dates and numbers | `intentions/service.py:58-70` `describe_due` and digest formats are English-only: format with Babel/ICU by locale. | 12/24-hour preference. |
| Currency | Spend stays in USD internally (provider prices are USD). Display text stops hard-coding "US$" (`pipeline.py:284`, `cli.py:99-137`): format by locale. | Billing in the tenant's currency (Stripe multi-currency, Stripe Tax for VAT/GST); `tenant.billing_currency` exists from day one. |
| AI transparency (EU AI Act Art. 50, in force since 2 Aug 2026) | Every first contact from someone who is not the owner gets a disclosure: "I'm Ava, an AI assistant for <owner>". Today's stranger reply and first-message reply must include it. The owner's hire notice already says Ava is an AI. | Machine-readable marking of AI-generated content where Art. 50(2) applies. |
| Consent and notice | Hire notice versioned per instance (exists). Contacts who talk to Ava get a short notice and a way to ask what is stored about them and to have it deleted (via the owner). Record the lawful basis per instance. | Consent records per Data Principal for DPDP (applies ~May 2027); consent manager integration if needed. |
| Export and deletion | Per instance: **export** (JSON of memories, contacts, preferences, conversations, intentions, audit) and **delete** (hard delete of all rows with the instance's `agent_id`, vault rows, jobs; tenant deletion when the last instance goes). Deletion is a job that runs inside the instance scope and leaves only a tombstone audit row. | Per-tenant data keys for crypto-shredding of backups; automated retention limits. |
| Residency | `tenant.region` and `agent_instance.region` columns now; one region in practice. Privacy notice lists sub-processors (hosting, Anthropic or the owner's chosen provider, email provider). | Cell architecture: one deployment and database per region (EU, India, US); a small global directory maps accounts to their region. Providers with regional processing where offered. |
| Scale | Pooling (PgBouncer transaction mode is safe with `set_config(..., true)`; turn off psycopg server-side prepared statements or use `prepare_threshold=None`); fair job claim; per-instance rate limits (requests and model calls per minute) in Postgres. | Read replicas; partition `event`/`audit_log` by time; move hot rate limits to Redis if Postgres becomes the bottleneck. |
| Channels by region | Telegram and Slack (and the web app). | LINE (Japan, Taiwan, Thailand) as a per-instance channel like a BYO bot; Discord; Microsoft Teams; email; SMS. **WhatsApp Business is not permitted for a general-purpose AI assistant** under Meta's terms since 15 Jan 2026. WeChat is out of scope (needs a Chinese entity, PIPL). |
| Accessibility | WCAG 2.2 AA for the app (keyboard, focus, contrast, labels), `prefers-reduced-motion` honoured by Motion, the voice intro always has a typed alternative. The European Accessibility Act has applied to consumer services in the EU since 28 June 2025. | Screen-reader audit; accessibility statement. |

## Work packages

Packages that touch files in flight elsewhere wait for those to merge. "Parallel" means it can start now
without conflicting with the providers (0007), memory (0008), reliability and deps agents.

| WP | Owns (files) | Delivers | Tests | When |
|---|---|---|---|---|
| **T0 Contract** | new `tenancy/{__init__,context,models}.py` (tables `tenant`, `agent_instance`, `instance_member`, `channel_endpoint`, `platform_state`), new `db/rls.py`, new Alembic revision (tables + dev migration, chained after the latest head at merge time) | Scope context var, `use_instance`, new tables, dev data migration | Migration on a seeded copy keeps every row; context var unit tests | **Parallel now** (new files; only the Alembic chain needs rebasing) |
| **T1 RLS + roles** | `db/session.py` (`after_begin`), `db/models.py` defaults (`_tenant`/`_agent`, two small functions), RLS revision, `deploy/postgres/` init (roles), `tests/tenancy/` + a conftest helper | Fail-closed defaults, policies, app/owner/ops roles | Meta-test, two-instance reads, app role cannot bypass | After T0; the `db/models.py` edit is tiny but waits for 0007 WP3/WP5 to merge |
| **T2 Accounts + web resolution** | `auth/service.py`, `auth/routes.py`, `app/accounts.py`, `app/routes.py` (`owner_person_id` -> `current_instance`, `/agents`, `/hire`), `app/catalog.py` | Platform-wide accounts, one Ava per account, hire creates an instance, no "taken" | Two owners hire Ava; B gets 404 on A's ids | After 0007 WP4 (shares `app/routes.py`) |
| **T3 Instance runtime + vault** | `factory.py`, `main.py`, `vault/store.py`, `vault/crypto.py` (AAD), `app/connections.py`, `policy/killswitch.py`, `pipeline.py` (`_is_owner`, messenger choice) | `InstanceRuntimes`, per-instance credentials and freeze, platform freeze | Wrong-bot test; secret moved between instances fails to decrypt | **Wait** for 0007 WP4/WP5/WP9 |
| **T4 Identity + channels** | `identity/resolver.py`, `identity/linking.py`, `channels/{runner,telegram,slack}.py`, `memory/store.py` (`get_or_create_context` only), webhook routes | Endpoint routing, per-endpoint webhook secrets, per-instance identities, scoped invites | Same Telegram user in two instances; invite via the wrong bot rejected | **Wait** for 0007 WP9 and 0008 (memory/store) |
| **T5 Jobs + worker** | `jobs/{queue,worker,handlers}.py`, `proactive/digest.py` (`ensure_scheduled`, approvals count), `proactive/notify.py`, `skills/calendar.py` (token source) | Fair claim function, scoped handlers, per-endpoint poll locks | Fairness: one instance with 1000 jobs doesn't delay another's single job; handler scope test | Coordinate with the **reliability** agent (likely touches `jobs/`); otherwise parallel |
| **T6 Budget** | `brain/budget.py`, ledger columns | Per-instance limits and lock, account limit, platform guard, `usage_record` | Concurrent reservations across instances; guard refuses at cap; owner-key spend not counted in platform guard | **Wait** for 0007 WP3 |
| **T7 Memory + knowledge scoping** | `context/{audience,recall}.py`, `knowledge/{profile,store,learn,ingest}.py`, `memory/store.py` (`remember`) | Explicit `agent_id` predicates; `instance_owner` | Cross-instance `org`/`public` memory invisible | **Wait** for 0008 WPs |
| **T8 Platform console** | `api/admin.py`, `frontend/src/features/console/*` | Operator accounts, metadata views, break-glass with audit | Operator can't read content without a reason; audit row in the instance | After T1, T2 |
| **T9 Email verification** | new `notify/email.py`, `auth/*` (verify, reset), settings | Verified email before hiring and before invites/reset; provider adapter (Postmark, Amazon SES or Resend; open question) with a fake for tests | Unverified account can't hire; token single-use and expiring | **Parallel now** (auth files are not in flight) but merge before T2 |
| **T10 Global + privacy** | message catalog, `intentions/service.py` formatting, stranger disclosure, export/delete jobs, FTS `'simple'` arm (after 0008), sign-up time zone | Section "Global", now column | Export round-trip; delete leaves zero rows for the instance; disclosure in first reply | Stranger disclosure and tz default **parallel now**; FTS waits for 0008 |
| **T11 Deploy** | `docker-compose.prod.yml` (worker, roles, URLs), `compose.yaml`, `deploy/README.md` section 5, `deploy/bootstrap.sh` closing message, `DEPLOY_MODE` | Separate worker, app role, owner-code bootstrap | Deploy smoke test; app role has no BYPASSRLS | With DevOps, after T1 |
| **T12 Frontend + E2E** | `frontend/src/features/marketplace/*`, `lib/session-context.tsx`, `lib/api.ts`, instance in URLs; `e2e/src/{db,fixtures}.ts`, new `e2e/tests/isolation.spec.ts` | "Hire your own Ava" copy, instance switcher later, two-owner fixtures | E2E: two owners, each tells Ava a secret, neither sees the other's | Frontend after T2; the E2E suite can be written now against the target API |

Order: T0 -> (T1, T9 in parallel) -> after 0007 lands: T2, T3, T6 -> T4, T5 -> after 0008: T7, T10
FTS -> T8, T11, T12. Nothing ships to a public URL as "hosted" until T1-T7 and the isolation E2E pass.

## Consequences

- One more concept (the instance) in every entry point, but one place (`tenancy/context.py`) owns it,
  and a forgotten scope returns nothing instead of someone else's data.
- The database role change is a deploy-time step that must happen before RLS is useful.
- Owners who want Telegram today still create a bot with BotFather until managed bots ship.
- In-flight ADRs 0007 and 0008 need small amendments (instance keys, hosted-mode SSRF rule).
- Accounts are platform-wide, so an account belongs to no instance until it hires (issues 24 and 25).
  Sign-up makes no Person (`user_account.person_id` is empty until `join_as_owner`). Events about an
  account (sign-up, e-mail checks, password and address changes) go through `audit.account_audit`: into
  the account's own Ava's audit log once it has hired, and into the platform-level `account_audit` table
  before that. They are never filed under another owner's Ava.

## Decided by the owner (2026-10-22)

- **Many owners by default.** "Each owner's Ava behaves differently based on their own conversations":
  anyone who signs up can hire their own Ava (`ALLOW_MANY_OWNERS=true` is the default). `false` stays
  as the operator's switch for single-owner mode (a self-hosted server for one person: the first hire
  is the only owner, later hires are refused).

- **Model keys: bring your own (BYOK), or Superaide AI credits** (updated 2026-10-23; first decided
  BYOK only on 2026-10-22). Each owner connects their own AI provider key on the AI model page, or opts
  in to **Superaide AI credits**: the platform's own Claude key (`PLATFORM_ANTHROPIC_API_KEY`, else
  `ANTHROPIC_API_KEY`) on `PLATFORM_MODEL`, offered only when `PLATFORM_AI_ENABLED=true`.
  - The owner's own key always wins for any role it can serve; credits fill only the rest.
  - Credit-paid calls are recorded with `key_source = platform` and reserve against both the owner's
    allowance (`PLATFORM_OWNER_DAILY_USD`, `PLATFORM_OWNER_MONTHLY_USD`, rolling) and the platform's
    daily cap (`PLATFORM_DAILY_CAP_USD`), atomically (section 7). Running out gets an honest reply.
  - The platform key is read only from the server's environment: never in an owner's runtime
    settings, vault, logs or API responses, and never used for an owner who didn't opt in.
  - **The operator's own Ava uses the server key** as its own key, even when hosted: accounts listed
    in `PLATFORM_KEY_OWNER_EMAILS` get `ANTHROPIC_API_KEY` with `key_source = owner` and the normal
    per-instance budget, outside the credits allowance and the platform cap. Emails aren't verified,
    so only existing accounts are listed; `user_account.email_normalized` is kept in step with `email`
    by the model and a CHECK constraint (migration 0029).
  - Otherwise an owner with no key and no credits sees a clear "Connect your AI model" state; their
    Ava never falls back to another owner's key, and in hosted mode never to the `.env` key.
- **Telegram: each owner's own bot.** The owner pastes their BotFather token in Connections; it is
  stored per instance (T3), and T4 makes one channel endpoint per owner bot, with webhooks keyed by a
  per-bot secret (the "Now" row of section 9). Managed bots and a shared Superaide bot are out of scope.

## Open questions for the owner

1. **Pricing and billing** for Superaide itself: free tier? monthly plan or pay-as-you-go? Which
   currencies first? (Stripe assumed.)
2. ~~Whose Claude key~~: decided above (BYOK or Superaide AI credits).
3. ~~Telegram~~: decided above (each owner's own bot).
4. **One Ava per account?** Or can one account run several Avas (e.g. work and personal)? And can a
   company workspace have several members managing one Ava?
5. **Hosted and self-hosted both?** Keep the one-VPS deploy as a supported self-hosted edition?
6. **Region**: where is the first hosted region (Singapore, India, EU, US)? Do Indian or EU owners need
   in-region hosting at launch?
7. **Email provider** for verification and resets: Postmark, Amazon SES or Resend?
8. **Operator access**: may support staff ever read an owner's conversations (with a reason, audited,
   owner-granted), or never?
9. **Who is the data controller** for the people who message an owner's Ava: the owner (we are the
   processor), or both? This decides the privacy notice and the DPA. Needs counsel.

## Sources

- [Goodwin: EU AI Act transparency obligations now in force](https://www.goodwinlaw.com/en/insights/publications/2026/08/alerts-technology-dpc-eu-ai-act-transparency-obligations-now-in-force)
- [Baker Botts: Article 50 transparency obligations go live](https://www.bakerbotts.com/thought-leadership/publications/2026/september/eu-ai-act-article-50-transparency-obligations-go-live)
- [McCann FitzGerald: one month to go, EU AI Act transparency](https://www.mccannfitzgerald.com/knowledge/technology-and-innovation/one-month-to-go-eu-ai-act-transparency-compliance)
- [AZB Partners: DPDP Rules notified](https://www.azbpartners.com/?p=87799)
- [Deccan Herald: DPDP Rules phased over 12-18 months](https://www.deccanherald.com/india/centre-notifies-dpdp-rules-implementation-planned-in-phases-spread-over-12-18-months-3798465)
- [respond.io: WhatsApp general-purpose chatbot ban](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban)
- [Telegram: Managed bots](https://core.telegram.org/api/bots/managed-bots)
- [Telegram: bots.createBot](https://core.telegram.org/method/bots.createBot)
- PostgreSQL documentation, "Row Security Policies" and `set_config`; Slack API docs on Socket Mode and
  app distribution; Mem0, Letta and OpenClaw documentation on per-user and per-agent memory scoping.
