# Contributing to Superaide

Thanks for helping. This guide covers setting up, the conventions we follow, and what a pull request needs before it can merge. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

- [Set up](#set-up)
- [Branches and commits](#branches-and-commits)
- [The gate](#the-gate)
- [Pull requests](#pull-requests)
- [Rules that must not break](#rules-that-must-not-break)
- [Backend](#backend)
- [Frontend](#frontend)
- [Tests, end to end and evals](#tests-end-to-end-and-evals)
- [Architecture decisions (ADRs)](#architecture-decisions-adrs)
- [Adding a Capability](#adding-a-capability)
- [Issues and labels](#issues-and-labels)
- [Secrets and personal data](#secrets-and-personal-data)

## Set up

You need [uv](https://docs.astral.sh/uv/), Node 24 (Active LTS) and Docker.

```bash
make setup                          # backend, frontend and e2e dependencies, git hooks
cp backend/.env.example backend/.env
make db migrate                     # Postgres 17 + pgvector in Docker, then the tables
make api                            # terminal 1: API with auto-reload on :8000
make web                            # terminal 2: Next.js on :3000
```

`make` lists every command. To run everything at once the way the owner does, use `bash start.command` (`--reset` wipes the data, `--no-docker` uses an embedded Postgres). Without a Claude key Ava runs offline and spends nothing.

## Branches and commits

- `main` is always releasable. Work on a short-lived branch named after the change: `feat/triage-digest`, `fix/login-lockout`, `docs/deploy-guide`.
- Keep pull requests small and focused. Split refactors from behaviour changes.
- Commit messages and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`, in the imperative, saying what changed for users or developers.

```text
feat(triage): hold low-urgency messages for the evening digest
fix(auth): count failed logins per account, not per session
test(e2e): the operator's access rows in a fresh Safety log
docs: explain the audience gate
```

| Type | Use for |
| --- | --- |
| `feat` | New behaviour |
| `fix` | A bug fix |
| `perf` | Faster or cheaper, same behaviour |
| `refactor` | Code change with no behaviour change |
| `test` | Tests or evals only |
| `docs` | Documentation and ADRs |
| `build`, `ci`, `chore` | Dependencies, CI, tooling |

Common scopes are the areas: `auth`, `channels`, `recall`, `triage`, `console`, `deploy`, `e2e`, `ui`. Add co-author trailers (`Co-Authored-By: Name <email>`) when someone else, or an AI assistant, wrote part of the change.

## The gate

A pull request merges when CI is green. Run the same checks locally first:

| Check | Command |
| --- | --- |
| Lint and format (backend) | `cd backend && uv run ruff check . && uv run ruff format --check .` |
| Types (backend) | `cd backend && uv run mypy` |
| Tests (backend) | `cd backend && uv run pytest` |
| Migrations | `alembic heads` shows one head; on a fresh database `alembic upgrade head && alembic check` |
| Frontend | `cd frontend && npm run lint && npm run typecheck && npm run build` |
| End to end | `make e2e` |
| Secrets | gitleaks (pre-commit and CI) |

`make check` runs everything except end to end. CI also builds the Docker images and validates the production compose file and Caddyfile.

## Pull requests

Open a pull request against `main` and fill in the [template](.github/pull_request_template.md). It asks for:

- the Conventional Commit type, linked issues and the ADR the change follows;
- what changed, by area, and the checks you ran (eval pass^k and cost if prompts or models changed);
- migrations: revision, whether it is reversible, any backfill;
- the security and privacy checklist, spend impact, feature flags, before/after screenshots for UI changes, and a rollback plan.

[CODEOWNERS](.github/CODEOWNERS) requests a review automatically. Changes to authentication, the vault, tenancy, policy, migrations, Capabilities, CI and ADRs always get one.

## Rules that must not break

These come from the architecture and are checked in review:

- **Identity, access and actions are decided in code, never in prompts.** The model writes reply text and proposes actions; `policy/` decides.
- **The audience gate filters memories in SQL** before the prompt is built. Never put something in a non-owner prompt and then tell the model not to say it.
- **Instances are isolated.** New per-instance tables carry `tenant_id` and `agent_id`, are listed in `backend/src/superaide/db/rls.py`, and enable row-level security in their migration (ADR 0009).
- **Side effects need approval.** `write_external`, `send` and `spend` end in an approval card at the Infant and Child stages. Untrusted content (web pages, emails, files) taints the turn.
- **Spend cap.** Every model call reserves its worst-case cost against `MAX_SPEND_USD`. A model without a price in `brain/pricing.py` is refused.
- Strangers get a fixed reply and no model call. People are recognised by immutable platform IDs, never names.
- New behaviour ships behind a setting that is off by default (for example `TRIAGE_ENABLED`) until it has been tried.

## Backend

- Python 3.14 with uv. Add dependencies with `uv add <package>` (or `uv add --dev`) so `uv.lock` stays in sync.
- Format and fix with `make fmt`; type-check with `make typecheck`.
- Schema changes: edit `db/models.py`, run `make migration m="what changed"`, and read the generated file. Migrations are numbered (`0028_…`); if two branches add the same number, renumber yours. CI fails if models and migrations differ or there is more than one head.
- New `/app` routes must be classified in `tests/api/test_authz.py` (public, account-only, flag-gated or owner).

## Frontend

- Next.js 16 (App Router), React 19, TypeScript (strict), Tailwind CSS and shadcn/ui. Next 16 differs from older versions: check the docs bundled in `frontend/node_modules/next/dist/docs/` (see `frontend/AGENTS.md`).
- Routes live in `src/app/`, screens in `src/features/`. Mark components that use state, effects or browser APIs with `'use client'`.
- Talk to the API only through `src/lib/api.ts` (Axios to `/api`), or `src/lib/server.ts` from server components. The browser never calls the backend's port.
- UI copy: plain words, sentence case, active voice; errors say what to do next.

## Tests, end to end and evals

- **Backend tests** run against a real Postgres (`TEST_DATABASE_URL`, wiped on every run, so it must differ from `DATABASE_URL`) and a fake model, so they never spend money or call Claude, Telegram, Slack or websites. Add a test for every behaviour change; impostor and privacy scenarios go in `backend/tests/redteam/`.
- **End to end** (`e2e/`, Playwright) runs each stack in its own slot. See [e2e/README.md](e2e/README.md).
- **Known bugs** get a failing test first, marked `test.fail(true, 'BUG: …')` or `@pytest.mark.xfail(strict=True)`, and a row in [docs/test-findings.md](docs/test-findings.md). Fix by removing the marker and committing the fix and test together.
- **Evals** score Ava on scenarios in `backend/evals/scenarios/` (privacy, recall, citations, triage and more):
  - `make eval` runs them offline with the fake model, for free.
  - `make eval-live` runs them on the real model, three runs each (`k=1` for one), within a US$1 cap. Quote the pass^k and the cost in the pull request.
  - A scenario whose score drops is a regression: open a **Reliability or eval regression** issue.

## Architecture decisions (ADRs)

Read [docs/decisions/](docs/decisions/) before changing anything structural. Write a new ADR when a change affects the data model, security model, a public interface, or a dependency that is hard to reverse:

1. Copy the shape of an existing one: `NNNN-short-title.md` with **Status**, **Context**, **Decision** and **Consequences**.
2. Use the next free number and set the status to `proposed`.
3. Open it as its own pull request, or with the first change that implements it. It becomes `accepted` when merged.
4. To change a decision, write a new ADR that amends or supersedes the old one, and note that in both.

## Adding a Capability

A Capability (skill) is how Ava gains an ability. The protocol is [ADR 0006](docs/decisions/0006-skill-protocol.md):

1. Propose it first with the **Capability proposal** issue form, so effect classes, scopes and budget are agreed before code.
2. Create a folder with `SKILL.md` (the Agent Skills standard, unchanged) and `superaide.json`. The built-in ones in `backend/src/superaide/skills/builtin/` are examples.
3. In `superaide.json`, declare the `id` (`publisher/name`), `version`, `tier`, each tool with its MCP-shaped `inputSchema` and effect class, the `connectors` and the narrowest OAuth scopes, the `network` hosts, and the `budget`. It must validate against [`manifest.schema.json`](backend/src/superaide/skills/manifest.schema.json).
4. The runtime enforces the manifest: never rely on `SKILL.md` text to restrict what a tool does.
5. Add tests for the tools and eval scenarios for the behaviour, including a refusal at a stage that isn't allowed to use it.

## Issues and labels

Open issues with the forms (bug, feature, Capability, regression, documentation); blank issues are off. Labels are defined in [.github/labels.yml](.github/labels.yml) and synced by a workflow, so change them there, not in the GitHub UI:

- `type:*`: one per issue (bug, feature, capability, regression, docs, question, chore);
- `area:*`: the part of the product, matching the forms' Area dropdown;
- `priority:p0` to `priority:p3`, set in triage;
- `status:*`: triage, needs-info, accepted, blocked, duplicate, invalid, wontfix;
- plus `security`, `privacy`, `accessibility`, `good first issue`, `help wanted` and `dependencies`.

## Secrets and personal data

- Never commit `.env`, keys, tokens or real personal data. The pre-commit hooks and CI run gitleaks.
- Use obviously fake values in tests (for example `xoxb-1-abc-…`) and blur personal data in screenshots.
- Report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md).
