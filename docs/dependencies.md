# Dependency policy

Superaide runs on a fast-moving stack (Python 3.14, Next.js 16, React 19, Playwright, two LLM SDKs). Upstream churn is
taken on a schedule, in small reviewed steps, instead of arriving as a surprise CI failure or a security patch that
waited. Issue #418.

## The routine

| When | What | Who |
| --- | --- | --- |
| Every Monday | Dependabot opens one grouped pull request per ecosystem (backend, frontend, e2e, Docker images, GitHub Actions) for minor and patch updates. A major version is its own pull request. | Dependabot |
| Every Monday | The **Canary** workflow (`.github/workflows/canary.yml`) runs the backend tests on the next Python and the frontend build on the next Node. It is allowed to fail: a red canary is a warning, never a block. | CI |
| First of the month | The Canary workflow opens a "Monthly dependency review" issue with the checklist below. | CI |
| Within a week of a security advisory | Merge the patch (a Dependabot security update skips the schedule). | Maintainer |

A grouped update is merged like any pull request: green CI, the changelog of anything in the table below read first.

## The five riskiest dependencies

These have broken (or are most likely to break) a build without a deliberate code change. Read their changelogs every
month, even when Dependabot has nothing to propose.

| Dependency | Where | Why it is risky | Read |
| --- | --- | --- | --- |
| `psycopg` (async database driver, with SQLAlchemy) | `backend/pyproject.toml` | Our connection, pool and type handling sit on it; a behaviour change shows up as data bugs, not as import errors. | psycopg "What's new" and SQLAlchemy changelog |
| `anthropic` and `openai` (LLM SDKs) | `backend/pyproject.toml` | Model names, usage fields and error types change; the spend cap (`brain/spend/pricing.py`) depends on the usage numbers. | Each SDK's release notes |
| `next` | `frontend/package.json` | Next 16 App Router: caching, proxying `/api/*` and cookie handling decide whether sign-in works. Read `frontend/node_modules/next/dist/docs/` for the version in use. | Next.js release notes |
| `react` | `frontend/package.json` | React 19 changes rendering and ref rules; test libraries lag it. | React blog |
| `@playwright/test` | `e2e/package.json` | A new Playwright wants new browser builds; selectors and trace formats change. | Playwright release notes |

`scripts/check_dependency_policy.py` (run by `backend/tests/repo/test_dependency_policy.py`) fails when one of these is
removed from the project without being removed from this table, so the table cannot go stale.

## Monthly review checklist

1. Read the changelog of each dependency in the table above since the last review. Note anything marked breaking,
   deprecated or removed.
2. Look at the last four Canary runs. A red run names what the next Python or Node would break; open an issue per cause.
3. Merge or close every open Dependabot pull request older than two weeks. Closing needs a reason in a comment (and an
   `ignore:` entry in `.github/dependabot.yml` with the same reason when it is a deliberate hold).
4. Check the held majors listed under `ignore:` in `.github/dependabot.yml` (TypeScript 7, the Node major): is the hold
   still wanted?
5. Run `uv lock --check` in `backend/` and `npm ci` in `frontend/` and `e2e/` on a clean checkout: the lockfiles must
   install as committed.
6. Look at the Python and Node end-of-life dates for the versions we run (CI and the Dockerfiles).

## Lockfile policy

- `backend/uv.lock`, `frontend/package-lock.json` and `e2e/package-lock.json` are committed and are the source of truth.
  CI installs with `uv sync --locked` and `npm ci`, so a manifest and lockfile that disagree fail the build.
- A dependency change edits the manifest and the lockfile in the same commit (`uv add`, `npm install`), never the
  lockfile by hand.
- Ranges in the manifests are floors (`>=`) for libraries we trust to follow semver. An **exact pin** (`==`) needs a
  comment saying why (for example `fastembed==0.9.1`: it downloads models). A pin without a reason is removed.
- Docker images are pinned to a major or minor tag, which Dependabot moves.
- Never downgrade to fix a failure caused by a newer release without opening an issue that says what to change to run on
  the new version.

## The canary

`.github/workflows/canary.yml` installs from the lockfiles but with the next language runtime: the Python minor
after the one CI runs (`PYTHON_NEXT`, now 3.15) for the backend suite, and the next Node major after the one CI runs
(`NODE_NEXT`, now 26; CI runs 24) for the frontend build. Both versions are set at the top of the workflow. Both jobs have `continue-on-error: true`, so they never block a pull request or a merge. When
the next runtime is stable and the canary has been green for a month, move CI, the Dockerfiles and `@types/node`
together, and the canary advances to the following version.
