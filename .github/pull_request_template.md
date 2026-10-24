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
- [ ] New `/app` routes are classified in `tests/api/test_authz.py` (public, account-only, flag-gated or owner)
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

## Screenshots

<!-- For UI changes: drag images into the cells. For a flow, also attach a short screen recording or GIF. Blur personal data. -->

| Before | After |
| --- | --- |
| | |

- [ ] N/A: no UI change

## Rollback plan

<!-- How to undo it in production: revert the commit, turn the flag off, `alembic downgrade -1`, … -->

## Changelog

<!-- One line for the changelog: what changed, in plain words. CHANGELOG.md is regenerated from merged PR titles with `python3 scripts/changelog.py`, so make the PR title say it. -->
