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

## Plan

1. Change detection and the gate (this PR).
2. Cheaper runs: coverage only on main and Full, cached Playwright browsers, `--durations=25`, per-PR-ref cancellation (already so: `cancel-in-progress` is true only when the workflow is `CI`, so main pushes, Full and the called pipeline are never cancelled by a newer ref... see the PR for the exact check).
3. pytest-xdist with one database per worker.
4. Skipping e2e shards for backend-only changes that leave the API contract alone (optional).
