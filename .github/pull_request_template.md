<!-- PR title: a Conventional Commit, e.g. feat(triage): hold low-urgency messages for the digest -->

## Summary

<!-- One or two sentences: what changes for owners, senders, operators or contributors, and why. -->

## Type

<!-- Keep one. -->

- [ ] `feat`: new behaviour
- [ ] `fix`: bug fix
- [ ] `perf`: faster or cheaper, same behaviour
- [ ] `refactor`: no behaviour change
- [ ] `test`: tests or evals only
- [ ] `docs`: documentation or ADRs only
- [ ] `build` / `ci` / `chore`: tooling, dependencies, CI

## Authored with an AI agent

<!-- AGENTS.md, section 4. Write the tool and model (for example `Claude Code, Claude Sonnet 5.5`), or `No`. -->

- Agent:

## Linked issues and decisions

<!-- "Closes #123". Name the ADR in docs/decisions/ this follows or changes, or write "None". -->

- Closes #
- ADR:

## What changed

<!-- One row per area touched; delete the others. -->

| Area | Change |
| --- | --- |
| Backend | |
| Frontend | |
| Database | |
| Tests and evals | |
| Docs | |

## Tests

<!-- Every feat and fix ships tests (CONTRIBUTING.md, "Every change ships with tests"); CI fails a feat or fix without a test file. -->

- [ ] Tests added or updated: <!-- name the files -->
- [ ] Flag covered both off and on, or no flag
- [ ] Negative or redteam test for security-relevant code (identity, trust, memory audience, approvals), or not security-relevant
- [ ] Bug fix: a regression test that fails before the fix
- [ ] No tests needed because <!-- docs / refactor / ci / chore / deps, plus a one-line reason. For a feat or fix, a maintainer adds the label `tests-not-needed` -->

## How it was tested

<!-- The same checks CI runs. Tick what you ran; strike through what doesn't apply. -->

**Backend** (in `backend/`)

- [ ] `uv run pytest` exits 0, with a test for every behaviour change (`tests/redteam/` for privacy or identity)
- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run mypy`
- [ ] On a fresh database: `alembic upgrade head && alembic check`, and `alembic heads` shows one head

**Frontend** (in `frontend/`)

- [ ] `npm run typecheck`, `npm run lint` and `npm run build`

**End to end and evals**

- [ ] `make e2e` passes; a known bug is muted with `test.fail(true, 'BUG: …')` and listed in `docs/test-findings.md`
- [ ] `make eval` (offline, free): pass^k
- [ ] `make eval-live k=3` if prompts, models or retrieval changed: pass^k , cost US$

**Manual**

- [ ] Tried in the app or a channel: <!-- which flow -->

## Migrations

<!-- Or write "None". -->

- Revision: <!-- e.g. 0028_contact_notes -->
- Reversible: <!-- yes, downgrade tested / no, because … -->
- Data backfill: <!-- none / what it does and how long it takes on a large instance -->

## Security and privacy

- [ ] New tables carry `tenant_id` and `agent_id` and are in `db/rls.py` with RLS enabled in the migration (ADR 0009)
- [ ] New `/app` routes are classified in `tests/guards/api/test_authz.py` (public, account-only, flag-gated or owner)
- [ ] No secrets, tokens or real personal data in code, tests, fixtures or screenshots
- [ ] Untrusted content (web pages, emails, files, community tools) marks the turn as tainted
- [ ] Side effects (`write_external`, `send`, `spend`) go through an approval
- [ ] Identity, access and actions are decided in code, not in prompts
- [ ] No personal data or message content in logs
- [ ] Not applicable: no change to data, access or actions

## Spend impact

<!-- Model calls added or removed, which model, and the rough cost per message or per day. Or "None". -->

## Feature flags

<!-- The setting and its default (new behaviour ships off, e.g. TRIAGE_ENABLED=false). Or "None". -->

## Before and after

<!-- REQUIRED on every PR, enforced by the "Before and after images" CI check (scripts/check_pr_evidence.py, issue #662):
     an image under Before and an image under After, in this body. There is no "N/A": a backend-only change shows a
     rendered screenshot of the real behaviour (the failing-then-passing test output, the API response, the chat),
     made with `python3 scripts/render_evidence.py`. Text or a code block does not count. Publish the PNGs on the
     `evidence` branch (pr-<n>/before-*.png, after-*.png) and embed
     https://raw.githubusercontent.com/BeastxD7/superaide/evidence/pr-<n>/<file>.png, or drag them in here.
     Editing this description re-runs the check; no push needed. Only Dependabot is exempt. Blur personal data. -->

### Before (main)

<!-- ![before: what the reader sees on main](https://raw.githubusercontent.com/BeastxD7/superaide/evidence/pr-<n>/before-<name>.png) -->

### After (this branch)

<!-- ![after: what the reader sees on this branch](https://raw.githubusercontent.com/BeastxD7/superaide/evidence/pr-<n>/after-<name>.png) -->

## Where to see the change

<!-- URL, steps and env flags to reproduce it. -->

## Rollback plan

<!-- How to undo it in production: revert the commit, turn the flag off, `alembic downgrade -1`, … -->

## Changelog

<!-- One line for the changelog: what changed, in plain words. CHANGELOG.md is regenerated from merged PR titles with `python3 scripts/changelog.py`, so make the PR title say it. -->
