# Superaide

AI personal assistants you hire, who work for you across your chat channels.

You can't be everywhere at once. **Ava**, the first Superaide, fields the people who reach out when you're busy: it answers what you've allowed, takes proper messages for the rest, and interrupts you only for what you said is urgent. It starts at the **Infant** stage. It watches, asks and learns, and nothing it does reaches anyone else without your approval. It talks to you on the web and on Telegram and Slack, recognises who it is talking to, refuses impostors, keeps private things private, and cannot spend more than a cap you set on Claude API credit (US$10 by default).

[![CI](https://github.com/BeastxD7/superaide/actions/workflows/ci.yml/badge.svg)](https://github.com/BeastxD7/superaide/actions/workflows/ci.yml)

## What works today

- **Marketplace and hiring.** Browse agents, create an account, hire Ava. Accounts use Argon2id password hashes and httpOnly cookie sessions with sliding expiry, revocation, lockout, per-IP throttling, CSRF protection and an audit trail.
- **An introduction, the way a new assistant would.** Ava introduces itself and learns about you in short steps: about you (chat, a resume or links, or a **video call** where Ava speaks and your browser transcribes), the people who matter and who can always get through, what counts as urgent, quiet hours and how to reach you, what it may share, ground rules, and connections.
- **Knowledge with provenance.** Settings live in typed tables (contacts, owner preferences). Facts live in a notebook with a category and their source: chat, call, or a named file or link. Removing a file removes what was learned from it.
- **Owner-aware answers.** Every prompt carries a short brief about the owner, scoped to the audience. The owner sees their full settings. Anyone else gets only what the owner allowed to be shared, and a VIP is recognised.
- **Identity you can trust.** People are recognised by immutable platform IDs, never display names. Unknown accounts get stranger mode. A new channel is linked only with a one-time code sent to an existing trusted channel.
- **Connections in the app.** Connect Claude, Telegram and Slack without editing files. Each credential is checked with the service, encrypted (AES-256-GCM, master key in the OS keychain), shown only as a hint, and applied at once. WhatsApp is listed as unavailable: Meta's business terms restrict general-purpose AI assistants.
- **Spend cap and kill switch.** Every Claude call reserves its worst-case price first and is refused if it could exceed `MAX_SPEND_USD`. One switch stops all model calls.
- **Operator console** at `/console`: people and trust tiers, conversations, safety log, spend, the pause switch and an impostor simulator.

## Architecture

| Part | Stack | Runs on |
| --- | --- | --- |
| [`frontend/`](frontend) | Next.js 16 (App Router), React 19, Tailwind CSS 4, Motion, Axios | http://localhost:3000 |
| [`backend/`](backend) | Python 3.14, FastAPI (JSON API only), SQLAlchemy 2, Alembic, Claude | http://127.0.0.1:8000 (OpenAPI docs at `/docs`) |
| Database | Postgres 17 with pgvector | localhost:5432 |

The browser only ever talks to the Next.js server. Pages that should be public (the marketplace, agent profiles) render on the server; signed-in screens render in the browser. API calls go to `/api/*` on the same origin, and a route handler forwards them to FastAPI ([`frontend/src/app/api/[...path]/route.ts`](frontend/src/app/api/%5B...path%5D/route.ts)), so the session cookie always belongs to the app's own origin. Telegram and Slack connect to the backend directly; their listeners run inside the API process. More in [docs/architecture.md](docs/architecture.md).

## Run it

**On a Mac, no setup:** double-click `start.command` (or run `bash start.command`). It installs [uv](https://docs.astral.sh/uv/) and, if your Mac has no Node.js 20.9+, a private copy of Node (checksum-verified) in `.tools/`. It starts Postgres: in Docker if Docker Desktop is installed, otherwise an embedded Postgres. It then starts the API and the frontend and opens http://localhost:3000. Without a Claude key, Ava runs in offline mode and nothing is spent. Connect Claude later under **Connections** in the app.

- `bash start.command --reset` wipes the data first, to hire Ava from scratch.
- `bash start.command --no-docker` always uses the embedded Postgres in `.local-db/`.

**For development** you need [uv](https://docs.astral.sh/uv/), Node 22+ and Docker.

```bash
make setup                         # backend and frontend dependencies, git hooks
cp backend/.env.example backend/.env
make db migrate                    # Postgres 17 + pgvector in Docker, then the tables
make api                           # terminal 1: API with auto-reload on :8000
make web                           # terminal 2: Next.js frontend on :3000
make check                         # everything CI runs: lint, types, tests, build
```

`make` lists every command. To run the whole stack in containers: `make stack`.

## Repository layout

```text
backend/
  src/superaide/
    pipeline.py      verify → resolve → audience gate → think → policy → reply
    app/             the app API: catalog, hiring, introduction, chat, connections
    auth/            accounts, passwords, sessions, rate limits
    knowledge/       extraction from files and links, facts, people, preferences, owner brief
    identity/        resolver, linking codes, trust tiers
    context/         the audience gate (a SQL filter)
    memory/          episodic log and audience-tagged memories
    brain/           Claude client, pricing, spend ledger, prompts, the agent charter
    policy/          action rules, kill switch
    channels/        Telegram and Slack adapters, signature checks, listener runner
    vault/           encrypted credentials and checks against each service
    api/             admin API for the operator console
    db/              SQLAlchemy models and session helpers
  alembic/           database migrations
  tests/             pytest suite against a real Postgres; tests/redteam has the impostor scenarios
frontend/
  src/app/           routes (Next.js App Router): pages, layouts, the /api proxy
  src/features/      screens: marketplace, auth, hire, onboarding, workspace, console
  src/components/    shared UI (buttons, nav, the agent's orb, conversation)
  src/lib/           API client, session, server-side helpers
  src/proxy.ts       sends signed-out visitors to log in before private pages
deploy/postgres/     database init scripts
docs/                architecture, decisions, research
.github/             CI, Dependabot, templates
compose.yaml         local Postgres, or the full stack with --profile app
start.command        one-click start on a Mac
```

## Configuration

The backend reads `backend/.env` (see [`backend/.env.example`](backend/.env.example)). Credentials connected in the app override `.env` values. The most important settings:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` / `TEST_DATABASE_URL` | Postgres for the app and for tests (the tests wipe theirs) |
| `MAX_SPEND_USD` | Hard cap on Claude API spend (default 10) |
| `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `SLACK_*` | Optional; prefer connecting them in the app |
| `ADMIN_TOKEN` | Operator console access |
| `FRONTEND_ORIGIN` | The only browser origin the API accepts |
| `SESSION_COOKIE_SECURE` | Set `true` when served over HTTPS |

## Command line

Run from `backend/` with `uv run superaide <command>`:

| Command | What it does |
| --- | --- |
| `serve [--reload]` | The API on 127.0.0.1:8000 |
| `reset-password --email …` | New password for an account (signs it out everywhere) |
| `freeze` / `unfreeze` | Kill switch |
| `spend` | Spend against the cap |
| `hello` | One tiny real Claude call to check the key |
| `add-manager` / `add-colleague` / `unlink` | Register people and channels by hand |
| `telegram-poll` / `slack-socket` | Run a channel listener on its own |

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and conventions, and [SECURITY.md](SECURITY.md) for the security model and how to report a problem.

## Roadmap

1. Triage and escalation: decide when to interrupt the owner, using their urgent topics, VIPs and quiet hours.
2. Memory upgrade: embeddings and hybrid search, time-valid facts, consolidation (see [docs/research](docs/research)).
3. Editing preferences and people after the introduction.
4. The correction loop and the first measured gates from Infant to Child.

Proprietary. All rights reserved.
