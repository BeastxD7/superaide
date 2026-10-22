# End-to-end tests

Playwright tests that drive Superaide in a real browser against a real API and Postgres, with a fake model, so they cost nothing.

```bash
make e2e                                  # build the frontend, then run everything on slot 0
cd e2e && npx playwright test tests/smoke.spec.ts --project=desktop   # one file
npx playwright test --ui                  # watch and debug interactively
npx playwright show-report playwright-report/slot-0
```

## Slots: isolated stacks side by side

Each run uses one **slot** (`E2E_SLOT`, default 0):

| | Port or name |
| --- | --- |
| API | 8100 + slot |
| Web (Next.js) | 3100 + slot |
| Database | `superaide_e2e_<slot>`, created and migrated automatically |

Playwright starts both servers for the slot (see `playwright.config.ts`). Tests in a slot run one at a time, because each test starts from an empty database. For parallelism, run several slots at once (each test agent gets its own), or shard in CI (`--shard=1/3`).

Postgres comes from `E2E_PG_ADMIN_URL`. The default, `postgresql://agent:agent@localhost:5432/postgres`, matches `docker compose up -d db` and CI. The tests only ever create, wipe and use databases named `superaide_e2e_*`.

## Opt-in projects

- `E2E_PROXY=1 npx playwright test --project=proxy` runs the `/api` proxy contract tests (`*.proxy.spec.ts`). It starts `scripts/fake-upstream.mjs` and three `next start` servers in front of it (defaults, `TRUST_PROXY_HEADERS=true`, and an unreachable upstream), so it needs `npm run build` in `frontend/` first. No browser and no database. CI runs it once, on shard 1.

## Not covered yet

- Unit tests of the pure frontend logic (`safeNext`, `errorFromDetail`, the SSE parser in `streamChat`, the chat reducer): the frontend has no unit runner. Adding Vitest is the next step; until then these are exercised through the browser tests.
- axe-core runs on the signed-out pages, the chat and the dashboard pages. It does not yet cover `/meet` (owned by the voice introduction work) or the public `/a/<handle>` page.

## Writing tests

- Import `test`, `expect` and `query` from `src/fixtures.ts`.
- `api` gives a fresh database and API helpers that share the page's cookies, so `api.signup()` signs the page in.
- `owner` gives a signed-in owner who has hired Ava and finished the introduction. Use `test.use({ ownerState: 'hired' })` or `'account'` to stop earlier.
- Find things the way a person would: `getByRole`, `getByLabel`, visible text. Don't use fixed waits.
- Cover what should be refused as carefully as what should work.
- A known bug gets a test of the correct behaviour marked `test.fail(true, 'BUG: …')`, so the suite stays green until it's fixed.
- Files named `*.mobile.spec.ts` run on a phone-sized screen (Pixel 7).

The `e2e-tester` agent in `.claude/agents/` follows these rules. Give it an area and a slot.
