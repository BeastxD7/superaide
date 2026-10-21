# Superaide

An AI personal assistant that grows up like a child, with identity checks you can trust.

You can't be everywhere at once. Ava, the personal assistant, fields the people who reach out when you're busy: it answers what you've allowed, takes proper messages for the rest, and interrupts you only for what you said is urgent. It starts at the **Infant** stage: it watches, asks and learns, and nothing it does leaves your chat without your approval. This repo is **phase 1 of the plan: its body and its identity**. It can talk on Telegram and Slack, it knows who it is talking to, it refuses impostors, it keeps private things private, and it cannot spend more than **US$10** of Claude API credit.

## What works today

- **Channels.** Telegram (polling or webhook) and Slack (Socket Mode or HTTP). Every incoming message is verified (webhook secret or Slack signature) or dropped.
- **Identity.** People are recognised only by immutable platform IDs, never display names. Unknown accounts get *stranger mode*: a fixed reply, no memory, no model call, no actions.
- **Linking a new channel.** If someone says "it's Shashank" from a new account, Ava sends a one-time code to Shashank's *existing* trusted channel. Only typing that code into the new chat links it. Codes are 6 digits, expire in 10 minutes and lock after 5 wrong tries. Every link can be undone.
- **Audience gate.** "remember that …" in a private chat stores a memory visible only to the people in that chat. The filter runs inside the SQL query, so a group chat or another person never even shows it to the model.
- **Policy.** Authority comes from the database (`manager`, `colleague`), never from what a message claims. Publishing, messaging others, granting access and deleting memory need your approval, and are refused outright for anyone but the manager.
- **Spend cap.** Every Claude call reserves its worst-case price first and is refused if that could pass `MAX_SPEND_USD` (10 by default). The real price is settled from the API's token counts.
- **Kill switch.** One command or one button stops all model calls and replies.
- **Accounts.** Sign up and log in with email and password (Argon2id hashes), sessions in an httpOnly cookie that expire after 30 idle days and can be revoked, lockout after repeated wrong passwords, per-device throttling, CSRF protection, and an audit trail of every sign-in event. Hiring an agent needs an account; only the hiring account can see its workspace.
- **Getting to know you.** After hiring, Ava introduces itself and learns about you in eight short steps: about you (chat, upload a resume or add links, or a **video call** where Ava speaks and your browser turns your answers into text), the people who matter (and who can always get through), what counts as urgent, how and when to reach you (quiet hours), what it may share, ground rules, and connections.
- **What Ava knows, and from where.** Settings live in typed tables (`contact`, `owner_preference`); facts live in the notebook with a category and their source (chat, call, a named file or link). Removing a file removes what was learned from it. Files and links are read by the small model inside the spend cap; offline, the text is kept and read later.
- **Owner-aware answers.** Every prompt carries a short brief about the owner. The owner gets their full settings; anyone else gets only what the owner allowed to be shared; a VIP contact is recognised as such.
- **Connections in the app.** Connect Claude (API key), Telegram (bot token) and Slack (a pre-filled app plus two tokens) without editing files. Each credential is checked with the service, encrypted with AES-256-GCM under a master key kept in the macOS Keychain, shown only as a hint like `sk-…4f2a`, and applied at once. WhatsApp is listed as unavailable: Meta's business terms don't allow general-purpose AI assistants on business numbers outside the EEA and Brazil.
- **The app.** React, Tailwind CSS and Motion (Framer Motion): the marketplace, sign-up, hiring, the introduction, and a workspace with the chat, the notebook and connections.
- **Operator console.** At `#/console`: growth stage, people and trust tiers, conversations, a readable safety log, spend, the pause switch and an impostor simulator.
- **Tests.** 70+ tests, including the five impostor scenarios in `tests/redteam/`. They use a fake model, so they cost nothing.

## How it fits together

Three separate parts, the usual way:

| Part | What | Where |
| --- | --- | --- |
| Frontend | React 19 + Vite 8 + Tailwind CSS 4 + Motion, in `web/` | http://localhost:3000 |
| Backend | FastAPI, a JSON API only (no pages), in `src/superaide/` | http://127.0.0.1:8000 (docs at `/docs`) |
| Database | Postgres 17 with pgvector, in Docker | localhost:5432 |

The frontend calls the backend over HTTP with Axios (`web/src/http.ts`). The API address comes from `VITE_API_URL` (default `http://127.0.0.1:8000`; copy `web/.env.example` to `web/.env.local` to change it). The backend only accepts browser requests from the frontend's address (`FRONTEND_ORIGIN`, default `http://localhost:3000`). Telegram and Slack talk to the backend directly.

## Try it in one command

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed (`brew install --cask docker`) and opened once. Then paste this into Terminal:

```bash
bash ~/Desktop/superaide/start.command
```

It installs [uv](https://docs.astral.sh/uv/) if needed, stops any copy that is already running, starts Postgres (in Docker, or a built-in Postgres if Docker Desktop isn't installed), creates `.env` with an admin token, starts the backend API on port 8000 and the frontend on port 3000, and opens http://localhost:3000. With Node 20+ installed the frontend runs on Vite's dev server; without Node it serves the prebuilt files in `web/dist`. You land on the marketplace as a new visitor: open Ava's card, hire it, and Ava onboards you. Without an API key the brain runs in offline mode, so this costs nothing. The terminal also prints the API docs and operator console links. Press Ctrl+C to stop everything; run it again any time and you pick up where you left off.

- `bash start.command --reset` wipes the data first, so you can hire Ava again from scratch.
- Forgot your password? `uv run superaide reset-password --email you@example.com` sets a new one and signs that account out everywhere.
- `bash start.command --no-docker` uses an embedded Postgres in `.local-db/` instead of Docker.

## Quick start on your Mac, step by step

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/), [uv](https://docs.astral.sh/uv/) (`brew install uv`) and Node 22 or later (`brew install node`).

```bash
cd ~/Desktop/superaide
cp .env.example .env              # then set ADMIN_TOKEN (the comment shows how to make one)
docker compose up -d              # Postgres 17 with pgvector, on localhost only
uv sync                           # installs Python 3.14 and the dependencies
uv run alembic upgrade head       # creates the tables
uv run pytest                     # 70+ tests, offline, free
```

### Try it with no API key and no channels

```bash
uv run superaide serve            # backend API on http://127.0.0.1:8000 (keep this running)
# in a second terminal:
cd web && npm install && npm run dev   # frontend on http://localhost:3000 (console at #/console)
```

Open http://localhost:3000/#/console, paste your `ADMIN_TOKEN`, then add yourself in a third terminal so the scenarios have someone to protect:

```bash
uv run superaide add-manager --name "Shashank" --telegram-id 1001
```

Go to **Try an impostor** and run the scenarios. Without an API key the model is an offline echo, so this costs nothing. Start again from an empty database any time with `docker compose down -v && docker compose up -d && uv run alembic upgrade head`.

## Connect your real channels

**Telegram**

1. Message `@BotFather`, send `/newbot`, and put the token it gives you in `TELEGRAM_BOT_TOKEN` in `.env`.
2. Run `bash start.command` again. It now also connects Telegram (polling, so no public URL is needed).
3. In the app, choose **On Telegram too** during onboarding, or press **Connect** next to Telegram in your workspace. Open the link it gives you and press **Start**. The link works once, for 15 minutes, and from then on Ava knows it's you on Telegram.
4. Anyone else who messages the bot is treated as a stranger. An operator can also link an account by hand in the console's **People** page.

**Slack**

1. Create an app at [api.slack.com/apps](https://api.slack.com/apps) from `slack-manifest.yml`.
2. Generate an app-level token with `connections:write` (`SLACK_APP_TOKEN`), install the app, and copy the bot token (`SLACK_BOT_TOKEN`). Put your workspace's team ID (starts with `T`) in `SLACK_TEAM_ID`.
3. Run `bash start.command` again; it connects Slack too.
4. Link Slack to yourself the way a real user would: DM Ava "it's Shashank". The code arrives on Telegram. Type it in Slack and the two channels become one person.

## Real Claude calls and the US$10 cap

1. Create a workspace in the [Claude Console](https://platform.claude.com/dashboard), make an API key, and set the workspace spend limit to US$10 as a second guard.
2. In `.env`, set `ANTHROPIC_API_KEY=...` and keep `MAX_SPEND_USD=10`. Restart `start.command`; the workspace shows Ava as online, and the console's *What is live right now* panel turns green for Brain.
3. `uv run superaide hello` makes one tiny Haiku 5.5 call (a small fraction of a cent) to check the key. `uv run superaide spend` shows the total.

Replies to known people use Sonnet 5.5. My estimate is about a cent per reply with the current prompts, so US$10 covers several hundred to a thousand messages. When the cap is reached, Ava says it is paused and makes no further calls. Prices live in `src/superaide/brain/pricing.py`; a model missing from that table is refused.

## Commands

| Command | What it does |
| --- | --- |
| `uv run superaide serve` | The backend API (app, console and webhooks) on 127.0.0.1:8000 |
| `uv run superaide reset-password --email …` | Set a new password for an app account (signs it out everywhere) |
| `uv run superaide telegram-poll` | Receive Telegram messages without a public URL |
| `uv run superaide slack-socket` | Receive Slack messages over Socket Mode |
| `uv run superaide add-manager` / `add-colleague` | Register a person and their first channels |
| `uv run superaide unlink --identity <id>` | Undo a link |
| `uv run superaide freeze` / `unfreeze` | Kill switch |
| `uv run superaide spend` | Spend against the cap |
| `uv run superaide hello` | One tiny real Claude call |

## How the code is laid out

```text
src/superaide/
  pipeline.py        verify → resolve → audience gate → think → policy → reply
  channels/          Telegram and Slack adapters, signature checks
  identity/          resolver, linking codes, trust tiers
  context/           the audience gate (SQL filter)
  memory/            episodic log and audience-tagged memories
  brain/             Claude client, pricing, spend ledger, prompt and reply schema
  policy/            action rules, kill switch
  api/               admin API for the operator console
  app/               the person-facing app API: catalog, hiring, introduction, chat, connections
  auth/              accounts, passwords, sessions, rate limits
  knowledge/         what the agent knows: extraction from files/links, facts, people, preferences, owner brief
  vault/             encrypted credentials and the checks against each service
  db/                SQLAlchemy models (migrations in alembic/)
skills/charter.md    who the agent is and its hard limits (cached in every prompt)
tests/redteam/       the five impostor scenarios
web/                 the frontend: React + Vite on :3000, Axios client in src/http.ts,
                     the app in src/app and the operator console
```

## Safety notes

- The admin API listens on 127.0.0.1 only and needs `ADMIN_TOKEN`.
- `ENABLE_SIMULATOR=true` lets the console inject messages as any account. Keep it off anywhere but your laptop.
- `.env` holds your keys and is git-ignored. Never paste keys into chats or prompts.
- Tell colleagues what Ava remembers and why before you add them (Singapore PDPA notice).

## What comes next (phase 2)

Memory tiers (episodes, time-valid facts, a person model, skills), the correction loop (draft, your edit, a proposed lesson, your approval), the prediction log, the nightly sleep job with rollback, and the first measured gates from Infant to Child.
