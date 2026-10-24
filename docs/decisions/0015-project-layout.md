# 0015: Project layout: package by feature, one public door per module

Status: PROPOSED (plan and tooling only; no source file moves in this PR). The owner decides the questions in the last section before phase 1 starts.

## Context

The owner's request: the layout is flat and hard to navigate. Reorganise by module, nest only where it makes sense, keep schemas and types easy to find, make every module independent, split the tests per module so that a change in module X runs X's tests (and its dependents), because CI test time is the pain.

Numbers below are produced by `scripts/layout_report.py` (read-only, repeatable; run it again after every phase). The import graph is the one `scripts/select_tests.py` uses to choose tests in CI (`ast`, lazy and TYPE_CHECKING imports included), so the picture and the CI behaviour cannot disagree. Measured on main at 7ff6e502 (2026-10-24).

### Today, measured

| What | Number |
| --- | --- |
| `backend/src/superaide` | 46 package directories + 12 top-level files (`config`, `pipeline`, `factory`, `main`, `cli`, `runtime`, `audit`, ...), 308 `.py` files, 53,728 lines |
| Biggest packages (files) | brain 23, app 20, triage 16, skills 13, knowledge 12, tooltrace 12, policy 11, trust 11, evals 10 |
| `backend/tests` | 215 test files; 151 of them directly in `backend/tests/` (plus `api/` 60 files, `redteam/` 6, `live/` 2) |
| `e2e/tests` | 100 specs; 87 flat in `e2e/tests/`, 13 in `journeys/` |
| `frontend/src` | 232 `.ts/.tsx` files in 16 feature folders; `features/dashboard` has 35 files directly in it, `lib/` 38, `components/` 15 |
| Models | 15 `models.py` files that each own tables, plus `db/models.py` (1,034 lines, 48 classes, imports the others to fill the metadata); 74 Alembic revisions |

Fan-in (how many packages import it) and fan-out (how many it imports), highest first: `config` 40 in / 0 out, `audit` 39 / 2, `db` 48 / 4, `tenancy` 32 / 4, `app` 28 in / 27 out, `brain` 24 / 18, `auth` 22 / 7, `knowledge` 19 / 16, `identity` 19 / 9, `context` 19 / 7, `vault` 19 / 6, `policy` 17 / 13, `main` 0 / 35, `pipeline` 5 / 21, `evals` 1 / 21, `triage` 12 / 21, `proactive` 14 / 20. Full table: `docs/design/module-graph.md` (generated).

The leaves are real: `mirror`, `passport`, `school`, `push`, `rehearsal`, `memsocket`, `brainshare`, `dissent`, `drills`, `mcp` have fan-in 0 to 2. The platform is real too: `config`, `db`, `audit`, `tenancy` are imported by almost everything and import almost nothing.

### The cycles (the real reason CI is slow)

`layout_report.py` finds strongly connected components:

- Package level: ONE cycle of 39 of the 58 packages. At package granularity the project has no independent modules.
- File level: a 44-file cycle across 19 packages (`app.routes`, `app.connections`, `app.gmail`, `app.onboarding`, `app.stream`, `asklink.*`, `brain.loop`, `brain.capabilities`, `factory`, `pipeline`, `policy.actions/kinds/outcomes/footprint`, `skills.registry/calendar/gmail/gmail_inbox/receipts`, `knowledge.facts/ingest/learn/pending/connector_facts`, `triage.service/routes/digest`, `proactive.*`, `jobs.handlers`, `journal.*`, `reception.service`, `voice.gateway`, `rules.timetravel`, `school.playbook`, `forknight.night`, `memory.sleep`, `evals.*`). Smaller ones: 12 files (`brain`, `channels`, `context`, `jobs`, `knowledge`, `memory`, `proactive`, `triage`), 7 files (`audit`, `auth`, `db`, `jobs`, `tenancy`), and two pairs.

Who sits at the centre (cycle-internal importers): `factory` (imported from 16 files in the cycle: it builds the agent, and half the code calls `build_agent` lazily), `skills.calendar` (9), `policy.actions` (9), `skills.registry` (6), `skills.gmail` (6), `knowledge.connector_facts` (6), `policy.kinds` (5), `asklink.service` (5), `triage.digest` (5).

What this costs, measured with `select_tests.py --changed`:

| Changed file | Backend tests selected (of 215) | Modules |
| --- | --- | --- |
| `channels/slack.py` | 192 | 38 |
| `memory/recheck.py` | 171 | 31 |
| `policy/actions.py`, `brain/loop.py` | 171 | 31 |
| `mirror/service.py` (a true leaf) | 80 | 3 |

A change to Slack runs 89% of the suite. Even a leaf runs 80 tests, because `app` and `console` import it and the route-wide guards come along. Sharper test ownership does not help while the graph is one blob; the graph has to be cut first.

## Decision

Organise the backend as a modular monolith, package by feature (Django apps, NestJS modules, bulletproof-react features). One folder per feature or integration owns everything about it: code, its types, its routes, its models, its tests. Four rules:

1. **Nest by domain group only when the graph supports it** (below), and never create a folder for one file.
2. **Public door.** A module is used only through its public entry: the package `__init__` (re-exporting its use-case functions), `api.py` for a larger surface, `schemas.py` for its types. Nobody imports `superaide.memory.sleep` from outside `memory/`. Enforced by `scripts/check_boundaries.py` (already in this PR, warn-only, baseline `ci/boundary-baseline.json` of 840 existing violations). Phase 9 turns it into a ratchet (no new violation) and finally an error. Why a home-grown check and not import-linter or tach: it reuses the same graph as the test selector (one definition of "imports"), needs no new dependency or Rust/Python 3.14 compatibility check, and CI already runs it with `uv run --python 3.14 --no-project`. import-linter (contracts for layers and forbidden imports) is the fallback if the owner prefers a maintained tool; the baseline file converts to its `ignore_imports` list.
3. **Dependencies point one way.** `platform/` and `contracts/` import nothing from feature modules; features import `platform/`, `contracts/` and lower groups; nobody imports `api/` or `main`. Cycles are removed by ports, not tolerated.
4. **Types are easy to find.** Each module has `schemas.py` (pydantic request/response and domain types; a `schemas/` folder only when it exceeds about 300 lines) and `models.py` (its tables). Types used by two or more modules live in ONE place, `contracts/`, together with the ports (Protocols) that break cycles. Rule of thumb: a type moves to `contracts/` only when a second module needs it, never preemptively.

### Target tree (backend)

Grouping is decided from the graph: groups are what the cycle analysis and fan-in/out say belong together, not taste.

```
backend/src/superaide/
  main.py  cli.py                 composition roots only (the only places that import everything)
  platform/                       fan-in 30-48, fan-out 0-4: safe to depend on
    config.py  runtime.py  pagination.py  envelope.py
    db/        engine, session, rls, base.py (Base + naming convention), registry.py (imports every module's models)
    tenancy/   audit/  security/  observability/
  contracts/                      cross-module types and ports, nothing else
    ports.py       AgentFactory, Messenger, ActionKindRegistry, EventBus (Protocols)
    messages.py    InboundMessage, OutboundMessage, Channel, Principal types
    events.py      domain events (ApprovalDecided, FactLearned, ...)
  identity/                       identity, principal, auth/accounts, vault (credentials): who is asking
  channels/
    base.py  runner.py  health.py            shared adapter code
    slack/  telegram/  web/                  one folder per channel (worked example below)
  integrations/
    google/   OAuth, sign-in (today connectors/google*.py)
    gmail/    client + skill + inbox + routes (today connectors/gmail, skills/gmail*, app/gmail)
    calendar/ client + skill + watch (today skills/calendar, proactive/calendar_watch)
  memory/                         one memory subsystem (worked example below)
    store  recall/  consolidation/  knowledge/  audience.py  schemas.py  models.py
  brain/
    loop.py  turn.py  citations.py  taskplan.py  verify.py
    providers/   anthropic, openai_compat, fake, presets
    spend/       budget, pricing, price_tables, platform_spend
    prompts/     charter.md, receptionist.md
  skills/                         registry, manifest, builtin/, payouts, receipts (marketplace)
  tooltrace/
  policy/        decide, kinds registry, brake, breaker, killswitch
  approvals/     binding, quote, outcomes, recipients, footprint (split out of policy)
  trust/  mandate/  rules/  actionlog/
  triage/  asklink/  reception/  proactive/        the owner-facing workflows
  jobs/                           worker, handlers (handlers call module public entries only)
  features/                       true leaves, one folder each, no nesting of their own
    mirror  passport  school  rehearsal  drills  dissent  forknight  brainshare  imports  journal  push  voice  memsocket
  api/                            composition of FastAPI routers only; each module owns its routes.py
  evals/                          dev tooling, never imported by shipped code
```

Every folder above that is not a leaf in `features/` keeps `routes.py` for its HTTP endpoints. This dismantles `app/` (20 files, fan-out 27): `app/gmail.py` goes to `integrations/gmail/routes.py`, `app/passport.py` to `features/passport/routes.py`, `app/skill_*.py` to `skills/`, `app/journal.py` to `features/journal/`, and `api/` only does `include_router`. The modules the owner named are covered: `channels/{slack,telegram,web}`, `integrations/{gmail,calendar,google}`, `memory` (+ knowledge), `policy` + `approvals`, `brain/{providers,...}`, identity/tenancy/auth, `jobs`, `platform`, and one `contracts`.

What is deliberately not nested: the 13 `features/*` leaves (3 to 8 files each: a sub-folder of one file helps nobody), and `tooltrace`, `trust`, `mandate`, `rules`, `actionlog` stay top-level modules, not folders inside `policy`, because their fan-in is 2 to 4 and nesting would hide real dependencies.

### Worked example 1: Slack

Before (five places, 283-line file, tests mixed into 151 flat files):

```
src/superaide/channels/slack.py            signature check, event parsing, SlackMessenger, socket mode (283 lines)
src/superaide/channels/slack_manifest.yml
src/superaide/channels/format.py           322 lines, Slack and Telegram formatting mixed
src/superaide/identity/linking.py          link codes (all channels)
src/superaide/app/connections.py           Slack connect/disconnect routes
src/superaide/tenancy/endpoints.py         slack_key
tests/test_slack.py  test_slack_link.py  test_slack_instances.py  test_slack_owner_channels.py
tests/test_slack_thread_history.py  test_channel_runner.py ...      (1,314 lines among 151 flat files)
e2e/tests/slack-link.spec.ts   frontend/src/features/dashboard/ConnectionsPage.tsx (35-file folder)
```

After:

```
src/superaide/channels/slack/
  __init__.py       public door: SlackMessenger, run_socket_mode, parse_event, SlackConnection
  client.py         SlackMessenger, SlackWorking (outbound)
  events.py         verify_signature, parse_event, endpoint_of, socket mode (inbound)
  formatting.py     Slack mrkdwn half of format.py
  linking.py        Slack workspace/instance linking, slack_key (the Slack end of identity linking)
  routes.py         connect / disconnect endpoints (from app/connections.py)
  schemas.py        SlackConnectBody, SlackEvent, ...
  manifest.yml
tests/channels/slack/
  test_client.py  test_events.py  test_link.py  test_instances.py  test_owner_channels.py  test_thread_history.py
e2e/tests/channels/slack-link.spec.ts
```

The same shape for `telegram/` and `web/`; `channels/base.py` keeps the `Messenger` port (it moves to `contracts/ports.py` in phase 2). Splitting `slack.py` into `client.py`/`events.py` is justified (outbound and inbound have different dependencies and tests); if the owner dislikes the extra files, `slack/` can start as `slack/__init__.py + slack/client.py`. Importers: `channels.runner`, `factory`, `main`, `cli`, `proactive.notify` use `from superaide.channels.slack import SlackMessenger` only.

CI effect: today a Slack change selects 192 of 215 tests. After phases 2 and 5 the dependents of `channels.slack` are `channels/runner` and the composition roots (which depend on the `Messenger` port, not the file); the selection is the Slack tests (about 8 files), the runner tests, the channel contract tests and the route-wide guards.

### Worked example 2: memory

Before (memory is spread over four packages, 28 files, and the cycle runs through it):

```
src/superaide/memory/        store, sleep, merge, recheck, importance, echo          (7 files)
src/superaide/context/       audience, recall, vectors, lexicon                      (5)
src/superaide/knowledge/     ingest, extract, facts, learn, pending, profile, store,
                             chunking, connector_facts, erase, trust                 (12)
src/superaide/db/models.py   the memory/knowledge tables, 48 classes shared with everything else
tests/test_sleep.py test_recall.py test_importance.py test_audience.py test_learn.py test_profile.py test_erase.py test_chunks.py ...
```

After:

```
src/superaide/memory/
  __init__.py        public door: remember, recall, forget, owner_brief, learn_from_source
  schemas.py         MemoryItem, Audience, Fact, Recalled  (shared ones re-exported from contracts/)
  models.py          memory, fact, source tables (moved out of db/models.py; same table names)
  store.py  audience.py
  recall/            retrieval.py (was context/recall), vectors.py, lexicon.py
  consolidation/     sleep.py, merge.py, recheck.py, importance.py, echo.py
  knowledge/         ingest.py, extract.py, facts.py, learn.py, pending.py, profile.py, chunking.py, erase.py
tests/memory/
  test_store.py  test_audience.py  recall/test_recall.py  consolidation/test_sleep.py  knowledge/test_learn.py ...
```

`knowledge/connector_facts.py` (facts learned from Gmail/Calendar) leaves the memory module: it moves into `integrations/gmail/` and calls `memory.learn_from_source` (today it imports `skills.calendar`, `skills.gmail_inbox` and `factory`; this edge is one of the cycle's hubs). `knowledge/trust.py` joins `trust/`. The audience gate stays a single SQL filter inside `memory/audience.py`: privacy decisions stay in code, as CLAUDE.md requires; the move must not change a single query (phase tests assert the redteam suite is unchanged).

## How the cycle is broken (before any file moves)

Techniques, in the order they pay off. Each is a small PR that changes no behaviour and adds no folder.

1. **`factory` becomes a port.** 16 cycle files import `build_agent`/`Agent` lazily. Define `AgentFactory` (Protocol) in `runtime.py`/`contracts/ports.py`, set at startup by `main`/`cli`; callers use `runtime.agent()`. `factory` then has fan-in 2.
2. **Action kinds are registered, not imported.** `policy.actions` imports `skills.calendar`, `skills.gmail`, `asklink.service`, `voice.gateway` to know what an action does. Invert it: each skill/module calls `policy.register_action_kind(kind, handler)` at import of its own package; `policy` only knows the registry. Removes `policy -> skills/asklink/voice/rules`.
3. **Skills registry stops importing gmail/calendar.** `skills.registry` is the plug-in point (ADR 0006); built-ins register themselves from `integrations/*` instead of the registry importing them.
4. **Event hooks for "after X, do Y".** `knowledge.connector_facts`, `triage.digest`, `proactive.digest`, `journal.extract` are post-action reactions. Replace direct calls with an in-process `EventBus` in `contracts/events.py` (`publish(FactLearned(...))`, subscribers registered by the owning module). Synchronous, same transaction, no new infrastructure; a module that emits events knows nobody.
5. **Routes move into their module.** `app.routes` (the 5th-largest hub) imports 20+ services only to expose them. Each module registers its own router; `api/` composes. This also removes the second-biggest source of fan-in noise for CI (`app` and `console` come along with every selection).
6. **Small cycle: `audit`/`auth`/`db`/`tenancy`/`jobs` (7 files).** Pull the shared session/RLS helpers into `platform/db` and let `audit` take a `session` argument rather than import `auth`.

Success measure (each PR): `layout_report.py` shows the file-level cycle shrinking; target is zero cycles larger than 2 files and a package graph that is a DAG. Today: 44 / 12 / 7 / 2 / 2.

## Move plan, in phases ordered by risk

Each phase is ONE atomic PR, prepared by a codemod (`scripts/codemod_move.py`, libcst for the import rewrite, `git mv` for every file so history follows; the codemod also writes `ci/layout-moves.json` for `rebase_after_move.py`), followed by ruff isort. Each PR must be green on the full suite (GREEN-FIRST) and is merged with a merge commit.

| Phase | What | Risk | Compat shim | CI effect (estimate; verify with `select_tests.py` after the phase) |
| --- | --- | --- | --- | --- |
| 0 | This PR: ADR, `layout_report.py`, `check_boundaries.py` (warn-only), module graph, `rebase_after_move.py` | none | n/a | none |
| 1 | Tests: `git mv` backend tests into `backend/tests/<module>/` mirroring TODAY's package names (`tests/memory/`, `tests/channels/`, ...), `conftest.py` per module for its fixtures; e2e specs into `e2e/tests/<module>/`; update `ci/modules.toml` globs (`tests/<module>/**`), drop most `# ci-module` lines | low: no src change; only test discovery, shard timings (`e2e/shard-timings.json`) and conftest scope | none needed | none yet, but ownership becomes a directory rule so a new test cannot be mis-mapped |
| 2 | Break the cycle in place (the six techniques above, one PR each, 6 to 8 small PRs) | medium: touches behaviour-critical `pipeline`/`policy`; protected by the redteam suite | n/a | the big one: Slack change 192 -> about 60 tests, `memory/recheck` 171 -> about 45, `policy/actions` 171 -> about 70, leaf 80 -> about 25 (guards + the leaf's own). The `app`/`console` reverse dependency disappears when routes move (technique 5) |
| 3 | `platform/` (config, db, tenancy, audit, runtime, pagination, envelope, security) and `contracts/` | medium-high: every file imports `superaide.config`/`superaide.db`; `uvicorn superaide.main:app` and the Dockerfile CMD stay unchanged | yes: `superaide/_compat.py` meta-path alias maps `superaide.config` -> `superaide.platform.config` for one release, so open branches and string references (monkeypatch targets, `logging.getLogger("superaide.skills.receipts")`) keep working | none (these are `core` modules and still run everything; this is the cost of a shared platform) |
| 4 | `features/*` leaves (13 packages) | low: fan-in 0 to 2 | yes (alias) | leaf change: about 25 tests |
| 5 | `channels/{slack,telegram,web}`, `integrations/{google,gmail,calendar}`, routes moved from `app/` | medium | yes | Slack change about 15 to 25 tests |
| 6 | `memory/` (+ context + knowledge), `brain/{providers,spend,prompts}`, `policy` + `approvals`, `identity`, `triage` group | medium-high: privacy-critical code (audience gate, vault); CODEOWNERS paths change | yes | memory change about 20 to 30 tests |
| 7 | Split `db/models.py` into per-module `models.py`; `platform/db/registry.py` imports them all so `alembic/env.py` (`from superaide.db.models import Base`) and autogenerate still see every table. Table names, columns and constraints do not change, so NO migration is expected; `alembic check` proves it | high: the one phase that can silently drop a table from the metadata | yes (`superaide.db.models` re-exports `Base` for one release) | removes the "any models.py change runs everything" rule for modules whose tables are private (`[full]` keeps the shared base only) |
| 8 | Frontend: `features/<name>/{components,hooks,api,types,tests}`; break up `features/dashboard` (35 flat files) into `home`, `approvals`, `skills`, `brain`, `connections`, `chat`, `settings`...; `lib/` split into `lib/http` and per-feature `api/` | low-medium: path alias `@/features/*` makes it mechanical | none (Next.js build catches every miss) | vitest and e2e selection by feature |
| 9 | Boundary check ratchet then error; remove aliases and shims; docs, README, CLAUDE.md, CODEOWNERS final | low | removed | none |

Estimated total effect (estimate, to be confirmed phase by phase): a typical single-module PR runs 15 to 60 of 215 backend tests instead of 170 to 190, so the backend shards drop from two 5 to 6 minute shards to one shard of about 1 to 2 minutes on those PRs; the full suite still runs on main and the scheduled Full run, so protection is unchanged. Full-suite time itself does not shrink; only PR time does.

### Compatibility shim

`superaide/_compat.py` installs a `sys.meta_path` finder that maps an old dotted name to the new one (`importlib` returns the new module object under both names), generated from `ci/layout-moves.json`. One release, then deleted in phase 9. It is cheap and also covers pytest `monkeypatch.setattr("superaide.x.y", ...)` strings. Not covered: file-path strings (see risks).

### Freeze protocol and open PRs

Open PRs on 2026-10-24: none (`gh pr list --state open` is empty; the last merged was #397). So today the move would conflict with nothing; that changes as soon as agents resume. Protocol:

1. **Announce** in the coordinator channel and in `AGENT-RULES.md`: before each phase, a freeze window opens: no new PR is opened that touches the paths of that phase (listed in the phase PR description, derived from `ci/layout-moves.json`).
2. Windows: phase 1 about 1 hour (tests only), phases 3 and 7 about 2 hours each (everything imports them), others 1 hour. Agents with open PRs touching the moved paths finish and merge first, or wait.
3. The move PR is merged by the coordinator on a green full suite; the codemod is re-run on the fresh `origin/main` immediately before merge (never apply a stale diff).
4. After the merge, every agent runs `scripts/rebase_after_move.py`: it takes the branch's own changes since the pre-move merge-base, remaps old paths and module names, and 3-way merges them into the moved files on the new main (conflict markers only where the same lines were edited). Tested in `backend/tests/test_layout_tools.py` on a throwaway repo.
5. Phases touching only tests (1) or only leaves (4) can run without a freeze if no open PR touches those paths (check with `gh pr list --json files`).

## Risks

- **Alembic.** `alembic/env.py` imports `superaide.db.models.Base`; 37 of the 74 revisions import `superaide.db.rls` (`enable`/`disable`), so the module path `superaide.db.rls` is FROZEN: when `db/` moves to `platform/db/`, `superaide/db/rls.py` stays as a permanent one-line re-export (the alias shim is not enough for a permanent API, and rewriting applied revisions is not allowed). No revision imports models. Phase 7 is guarded by `alembic upgrade head && alembic check` on a fresh DB and the downgrade round trip CI already runs. Never edit an existing revision.
- **Row-level security and tenant columns.** Moving a model must keep `tenant_id`/`agent_id` and the RLS statements; the existing tests for every table stay green or the phase is not merged.
- **`security/surface.lock.json` and `surface.toml`.** The lock hashes protected files by path; moving `triage/policy.py`, `context/audience.py`, `brain/budget.py` and others changes their path and hash. Each phase that moves a protected file regenerates the lock in the same PR (`python -m superaide.security.surface --update`) and says so in the PR description; this is a reviewed change, not a bypass.
- **`ci/modules.toml`.** Rewritten in phases 1 to 7; `test_select_tests.py` already fails on unmapped or doubly-mapped files, which is the safety net. `[full]` globs that name `backend/src/superaide/**/models.py` keep matching.
- **Dockerfile and entry points.** `uvicorn superaide.main:app` and `cli.py` stay at the root; the package name does not change. `backend/Dockerfile` copies `src/`, so no path edits are expected; check `deploy/` and `docker-compose.prod.yml` anyway.
- **String references.** Logger names (`superaide.tooltrace`), `monkeypatch` string targets, `importlib` paths and `manifest.schema.json`/`charter.md` read relative to `__file__`: the codemod greps all `"superaide.` strings; resource files move together with their module (`Path(__file__).parent`). No pickled paths were found by grep (`pickle` is not used in `backend/src`).
- **CODEOWNERS.** Paths like `/backend/src/superaide/policy/` and `/backend/src/superaide/context/audience.py` must be updated in the same phase PR, otherwise review requirements silently stop applying to security code. Add a test that every protected path in `surface.toml` is matched by a CODEOWNERS line (cheap, recommended before phase 3).
- **Docs links.** `docs/`, README, CLAUDE.md, `docs/design/test-modules.md`, ADRs reference file paths; the codemod rewrites `*.md` references and a link checker pass runs in the phase PR.
- **Shard timings.** `e2e/shard-timings.json` is keyed by spec path; phase 1 regenerates it.
- **Drift during the plan.** 7 to 10 phases over several days with agents merging in between: mitigated by short windows and by `rebase_after_move.py`; if a phase cannot merge within its window it is abandoned and regenerated, never hand-merged.

## Pros and cons

Pros: the test selection gets sharper by an order of magnitude on typical PRs; a feature is one folder (code, types, routes, models, tests), which is also what an agent needs to read; boundaries are checked, so the structure stays this way; no behaviour change by design.

Cons: roughly 10 PRs plus cycle-breaking PRs of churn, each conflicting with parallel work for about an hour; two-three weeks of mixed old/new paths if phases are spread out (shim mitigates); `git blame` needs `--follow` (history is preserved by `git mv`, but `git log` without `--follow` stops at the rename); more `__init__.py` re-exports to maintain; event-hook indirection makes some flows harder to trace; the full suite does not get faster.

## Decisions the owner must make

1. Go / no-go for the plan, and whether phase 2 (cycle breaking, no moves) may start first. It gives most of the CI saving with the least churn, so it is recommended even if the later moves are postponed.
2. Boundary enforcement: keep the in-repo `check_boundaries.py` (recommended), or adopt import-linter. And when to flip warn-only to ratchet (recommended after phase 2) and to error (after phase 9).
3. Grouping names: `platform/`, `contracts/`, `channels/`, `integrations/`, `features/` for the leaves, versus a flat top level. In particular: `skills/` top-level (recommended: marketplace code, not brain internals) or `brain/skills/`; `approvals/` split from `policy/`.
4. Freeze windows: accept the 1 to 2 hour windows, or run the moves overnight when no agent is active (recommended: phases 3 and 7 overnight).
5. Compatibility shim: one release (recommended), or none (cleaner, but any open branch breaks).
6. Rename `app/` away? Recommended: dissolve it into the modules (routes) and keep `api/` as the composer.
7. Frontend scope: do phase 8 now or after the backend; and whether `features/dashboard` is split in the same PR.
8. Package rename or `src/` layout changes are NOT proposed (the import name stays `superaide`).
