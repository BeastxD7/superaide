# 0013: A CI pipeline that runs only what a change needs

Status: PR 1 (change detection and one gate) built 2026-10-23; PRs 2 to 4 follow, see "Plan".

## Context

Every pull request ran the whole pipeline: backend (about 6 to 10 minutes), frontend, three Playwright shards, Docker images and the secret scan. A change to a README paid for all of it, and with about 25 open pull requests the runners are saturated. The owner asked which tests can be skipped when a change does not touch the code they cover.

## Decision

Use the industry-standard "changes + gate" pattern.

1. A `changes` job lists the files a pull request touches (dorny/paths-filter, pinned by commit SHA to v4.0.3, which reads the file list through the API and needs no checkout) and outputs `backend`, `frontend`, `e2e` and `docker`.
2. Each job has an `if:` on its output. The secret scan always runs.
3. A last job, `CI result`, runs `if: always()`, needs every other job and fails when any of them failed, was cancelled, or is in an unexpected state. A skipped job counts as success, unless its filter asked for it (that means an upstream problem, so it fails). This is the single check to require if branch protection is turned on; requiring the individual jobs would leave them pending forever when skipped.

| Changed path | Runs |
| --- | --- |
| `backend/**` | backend, e2e |
| `frontend/**` | frontend, e2e |
| `e2e/**` | e2e only |
| Dockerfiles, `deploy/**`, compose files, `.env.prod.example`, dependency files | images (plus backend/frontend when the file lives there) |
| Only `**/*.md`, `docs/**`, `LICENSE*`, `assets/**`, issue templates, CODEOWNERS, the PR template | secret scan and the gate |
| `.github/workflows/**`, `scripts/**`, `Makefile`, `.gitleaks.toml`, or any file no filter owns | everything |

Fail closed: the last row is the safety net. A path nobody classified runs the full pipeline rather than nothing.

Pushes to `main`, `workflow_dispatch`, `merge_group` and the scheduled Full run (full.yml calls ci.yml, so `github.event_name` is `schedule`) ignore the filters and run everything. Nothing on `main` or in Full is weakened. `merge_group` is added so a GitHub merge queue, if enabled, tests the merged result once.

## Why not the alternatives

- Workflow-level `paths:` filters: a skipped workflow leaves a required check pending forever, and they cannot express "run e2e when either half changed".
- A build system with an affected graph (Nx, Bazel, Turborepo): this is a two-package repository; path filters give the same result without adopting a tool.
- Per-test impact analysis for pytest: coverage-based selection is fragile and a missed test is a missed regression. Not worth it while the full backend run is a few minutes.

## Cheaper runs (PR 2)

- Coverage (`--cov`) runs on pushes to main, manual runs and Full only; pull requests and merge groups run plain pytest. Every run prints `--durations=25`.
- The Playwright browser is cached by `actions/cache`, keyed on the installed Playwright version. System libraries are not part of the cache, so `playwright install-deps` still runs on a hit.
- Concurrency: a pull request's newer push cancels its older run; runs on `main` and the called Full pipeline are never cancelled (before, a push to main could cancel the previous push's run).
- uv, npm and Docker layer caches were already in place and stay.

## Parallel backend tests (PR 3)

CI runs `pytest -n auto` (pytest-xdist). The suite truncates every table before each test, so each worker gets its own database, `<TEST_DATABASE_URL name>_gw0`, `_gw1`, and so on, created on demand from the server's `postgres` database by `tests/conftest.py` and never dropped. Locally nothing changes: plain `uv run pytest` is serial on `TEST_DATABASE_URL`; add `-n 4` to parallelise. The escape hatch for a failure that looks order- or parallelism-related is `uv run pytest -p no:xdist` (also in CI by editing the step). A test that cannot run in parallel is marked and fixed rather than skipping the parallel run.

## Run beside backend, not after it (PR A0)

`e2e` and `images` used to `need` backend, so a pull request's wall-clock was backend plus the slowest e2e shard (last green main run: backend 10:34 to 10:53, e2e 10:53 to 11:06). Neither job uses the backend job's output: e2e builds and starts its own API and web. They now need only `changes` and `frontend` (cheap, so a lint or type error still stops them early) and start in parallel with backend. The `CI result` gate still needs and requires backend, so nothing that protects main is weaker: a backend failure still fails the gate; it just no longer saves the runner minutes of e2e that would have been skipped.

## Balanced e2e shards and the Next cache (PR A)

Playwright's `--shard` splits by test count, and shard 2 held the slow specs (`intro-sources`, `introduction`): 20 minutes against 7 for the others. `e2e/scripts/shard.mjs` now places every spec file under `e2e/tests` (except the opt-in proxy and dev projects), slowest first, on the shard with the least time so far, using the measured seconds in `e2e/shard-timings.json`. A file missing from that file gets the average weight, so a new spec still runs on exactly one shard; an empty list fails the job. Four shards of about 5 minutes of tests each replace three. Refresh the timings from a full run: `npx playwright test --reporter=json > report.json` (with `PLAYWRIGHT_JSON_OUTPUT_NAME`), then `node scripts/shard.mjs --update report.json`; stale numbers only make shards slightly unequal, never skip a test.

`frontend/.next/cache` is cached (frontend and e2e jobs, keyed on lockfile plus sources, with a lockfile-only fallback). The e2e `npm ci` stays on the setup-node npm cache (23 packages, seconds). Two Playwright slots per shard were not added: the 4-shard split already brings the longest shard near 5 minutes, and sharing 4 vCPUs between two API, two Next and two browser stacks risks timing flakes for little gain; revisit if shards grow.

## Plan

1. Change detection and the gate (this PR).
2. Cheaper runs: coverage only on main and Full, cached Playwright browsers, `--durations=25`, per-PR-ref cancellation (already so: `cancel-in-progress` is true only when the workflow is `CI`, so main pushes, Full and the called pipeline are never cancelled by a newer ref... see the PR for the exact check).
3. pytest-xdist with one database per worker.
4. Skipping e2e shards for backend-only changes that leave the API contract alone (optional).
