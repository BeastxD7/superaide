# Superaide: notes for Claude Code

Superaide is a marketplace of AI agents you **hire**. They start at the Infant stage and grow (Infant → Child → Adolescent → Adult) as they earn trust. The first agent, **Ava**, is a **personal assistant**: the owner (say, a busy CEO) hires her, introduces themselves, and Ava then fields people who contact the owner on the web, Telegram and Slack. She answers only what the owner allowed, takes messages, and will interrupt the owner only for what they marked urgent.

Read `README.md` for the overview and `docs/architecture.md` for how a request flows. Read `docs/decisions/` before changing anything structural.

## Layout

- `backend/`: Python 3.14, uv, FastAPI (JSON API only), SQLAlchemy 2, Alembic, Postgres 17 + pgvector. Code is in `src/superaide/`; `pipeline.py` is the message path.
- `frontend/`: Next.js 16 App Router.
  - Routes are in `src/app/` and screens in `src/features/`.
  - The browser calls `/api/*`, which `src/app/api/[...path]/route.ts` forwards to FastAPI.
  - `src/proxy.ts` redirects signed-out visitors.
  - The signed-in app is `/dashboard/*`: a shadcn/ui sidebar shell (`src/features/dashboard/`) with Home, Chat, Notebook, People, Connections and Settings. The introduction (`/meet`) stays full-screen.
  - shadcn components live in `src/components/ui/`, with tokens mapped onto the violet palette in `src/styles/app.css`. Add more with `npx shadcn add <name>`. Site-wide pieces (nav, account menu, brand mark) are in `src/components/site.tsx`.
  - `frontend/AGENTS.md` (written by `next dev`) points to the Next.js docs bundled in `node_modules/next/dist/docs/`. Check them before using a Next API from memory; this is Next 16.
- `e2e/`: Playwright. One isolated stack per `E2E_SLOT` (API on :8100+n, web on :3100+n, database `superaide_e2e_n`).
- `.claude/agents/`: `e2e-tester` and `api-tester`. Run several `e2e-tester`s in parallel, one area and one slot each.
- `start.command`: one-click run on the owner's Mac (Docker or embedded Postgres; installs Node if missing).

## Commands

- `make` lists them all.
- Day to day: `make db migrate api web`.
- Before pushing: `make check` (lint, mypy, tsc, pytest, build). `make e2e` builds the frontend first, then runs the browser tests.
- Backend tests need `TEST_DATABASE_URL`, which must differ from `DATABASE_URL` because the test database is wiped.
- Schema changes: edit `db/models.py`, run `make migration m="…"`, and read the generated file. CI fails if models and migrations differ.

## Rules that must not break

- **Identity, access and actions are decided in code, never in prompts.** The model writes reply text and proposes actions; `policy/` decides.
- **The audience gate filters memories in SQL** before the prompt is built. Never put something in a non-owner prompt and then instruct the model not to say it. `tests/redteam/test_canaries.py` guards this.
- Strangers (unknown accounts) get a fixed reply and **no model call**.
- People are recognised by immutable platform IDs, never names. Linking a new channel needs a one-time code sent to an already trusted channel.
- **Spend cap:** every Claude call reserves its worst-case cost against `MAX_SPEND_USD` (US$10 for development). Tests use a fake model and never call Claude, Telegram, Slack or websites.
- Credentials live in the encrypted vault (`vault/`). Never log them, return them, or ask the user to paste them into a chat.
- No unofficial WhatsApp libraries. WhatsApp stays "unavailable" until an official route fits Meta's terms.
- The browser never calls the API port directly. Server components use `src/lib/server.ts`.

## Conventions

- Conventional Commits (`feat:`, `fix(auth):`, `test(e2e):` …), small commits, a test with every behaviour change.
- Copy in the UI: plain words, sentence case, active voice; errors say what to do next.
- A bug found by a test agent gets a failing test first (`test.fail(true, 'BUG: …')` or `xfail(strict=True)`), then the fix.
- **Open bugs are listed in `docs/test-findings.md`**, each with the test that proves it. Remove the marker, fix, and commit both together.

## Status (October 2026)

Done:
- Accounts and sessions, hiring, and the introduction: chat, files and links, or a video call with browser speech. Brave can't do speech-to-text, so it falls back to typing and says why.
- Knowledge with provenance, the owner brief, the credentials vault and in-app connections (Claude, Telegram, Slack).
- The Next.js port, the dashboard, CI, Docker images, and the E2E, API and journey test suites.
- `GET /app/people` and `GET /app/preferences` feed the dashboard. There are no editing endpoints yet.

Next, in order (from `docs/research/Personal assistant memory architecture.md`):
1. **Relevance retrieval:** full-text search (`tsvector`) plus reciprocal rank fusion inside the audience filter, `document_chunk` for long files, and a small evaluation set. Retrieval currently returns the newest 5 or 30 memories whatever was asked, and extraction reads only the first 12,000 characters of a file.
2. **Embeddings:** `halfvec(1024)` with a local model by default; pgvector 0.8.4 or later.
3. **Consolidation:**
   - New columns: `trust_rank`, `author_person_id`, `superseded_by`.
   - Conflicts surface in the notebook for the owner to resolve.
   - Erasing a source must really delete its text, chunks and facts.
4. **Triage:** Haiku labels each inbound message with quoted evidence, and a deterministic policy engine picks the outcome. Strangers can leave a message. Add VIP invite links.
5. **Escalation and digests:**
   - An escalation ladder with acknowledgement buttons on Telegram and Slack, plus two or three digests a day.
   - Also: row-level security, log redaction, and an AI notice at first contact.
6. Editing preferences and people from the dashboard: the People and Settings pages are read-only today.

## Working with the owner (Shashank)

- Claude Code on Shashank's Mac (`~/Desktop/superaide`) owns this repo from 21 October 2026: features, bug fixes and pushing to GitHub.

- Narrate progress as you go: what you're doing, what you found, what's next.
- He runs things with `bash ~/Desktop/superaide/start.command` on a Mac. Keep that path working, and test it from a fresh clone.
- The repo is private: `github.com/BeastxD7/superaide`.
