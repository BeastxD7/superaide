# Contributing

## Workflow

- `main` is always releasable. Work on a short-lived branch (`feat/triage`, `fix/login-lockout`), open a pull request, and merge once CI is green.
- Keep pull requests small and focused; split refactors from behaviour changes.
- Run `make check` before pushing. It runs exactly what CI runs.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: escalate urgent messages to the owner on Telegram
fix(auth): count failed logins per account, not per session
docs: explain the audience gate
```

Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`, `style`. Say what changed for users or developers, and why.

## Backend

- Python 3.14 with uv. Add dependencies with `uv add <package>` (or `uv add --dev`) so `uv.lock` stays in sync.
- Lint and format with ruff (`make fmt`); type-check with mypy (`make typecheck`).
- Schema changes need a migration: edit `db/models.py`, then `make migration m="what changed"`, and read the generated file. CI fails if models and migrations differ.
- Tests run against a real Postgres (`TEST_DATABASE_URL`, wiped on every run) and a fake model, so they never spend money. Add a test for every behaviour change; impostor and privacy scenarios go in `tests/redteam/`.
- Decisions about identity, access and actions belong in code, never in prompts.

## Frontend

- React with TypeScript and Tailwind CSS. Lint with `npm run lint`; `npm run build` type-checks.
- Talk to the API only through `src/http.ts` (Axios).

## Secrets and personal data

- Never commit `.env`, keys, tokens or real personal data. The pre-commit hooks and CI run gitleaks.
- Use obviously fake values in tests (for example `xoxb-1-abc-…`).
- See [SECURITY.md](SECURITY.md).
