# agi-agent

An AI hire that grows up like a child, with identity checks you can trust.

The agent (called Ava by default) starts at the **Infant** stage: it watches, asks and drafts, and nothing it does leaves your chat without your approval. This repo is **phase 1 of the plan: its body and its identity**. It can talk on Telegram and Slack, it knows who it is talking to, it refuses impostors, it keeps private things private, and it cannot spend more than **US$10** of Claude API credit.

## What works today

- **Channels.** Telegram (polling or webhook) and Slack (Socket Mode or HTTP). Every incoming message is verified (webhook secret or Slack signature) or dropped.
- **Identity.** People are recognised only by immutable platform IDs, never display names. Unknown accounts get *stranger mode*: a fixed reply, no memory, no model call, no actions.
- **Linking a new channel.** If someone says "it's Shashank" from a new account, Ava sends a one-time code to Shashank's *existing* trusted channel. Only typing that code into the new chat links it. Codes are 6 digits, expire in 10 minutes and lock after 5 wrong tries. Every link can be undone.
- **Audience gate.** "remember that …" in a private chat stores a memory visible only to the people in that chat. The filter runs inside the SQL query, so a group chat or another person never even shows it to the model.
- **Policy.** Authority comes from the database (`manager`, `colleague`), never from what a message claims. Publishing, messaging others, granting access and deleting memory need your approval, and are refused outright for anyone but the manager.
- **Spend cap.** Every Claude call reserves its worst-case price first and is refused if that could pass `MAX_SPEND_USD` (10 by default). The real price is settled from the API's token counts.
- **Kill switch.** One command or one button stops all model calls and replies.
- **Dashboard.** A React app showing the growth stage, people and trust tiers, conversations, a readable safety log, spend, and an impostor simulator.
- **Tests.** 33 tests, including the five impostor scenarios in `tests/redteam/`. They use a fake model, so they cost nothing.

## Try it in one command

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed (`brew install --cask docker`) and opened once. Then paste this into Terminal:

```bash
bash ~/Desktop/agi-agent/start.command
```

It installs [uv](https://docs.astral.sh/uv/) if needed, starts Docker Desktop if it is closed, runs Postgres 17 in Docker, creates `.env` with an admin token, adds two demo people (Shashank as manager, Priya Tan as colleague), and opens the dashboard at http://127.0.0.1:8000 already signed in. It uses the offline fake model, so it costs nothing. Press Ctrl+C to stop; run it again any time.

- `bash start.command --reset` wipes the demo data first.
- `bash start.command --no-docker` uses an embedded Postgres in `.local-db/` instead of Docker.

## Quick start on your Mac, step by step

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/), [uv](https://docs.astral.sh/uv/) (`brew install uv`) and Node 22 or later (`brew install node`).

```bash
cd ~/Desktop/agi-agent
cp .env.example .env              # then set ADMIN_TOKEN (the comment shows how to make one)
docker compose up -d              # Postgres 17 with pgvector, on localhost only
uv sync                           # installs Python 3.14 and the dependencies
uv run alembic upgrade head       # creates the tables
uv run pytest                     # 33 tests, offline, free
```

### Try it with no API key and no channels

```bash
uv run agi-agent serve            # API on http://127.0.0.1:8000 (keep this running)
# in a second terminal:
cd web && npm install && npm run dev   # dashboard on http://localhost:5173
```

Open the dashboard, paste your `ADMIN_TOKEN`, then add yourself in a third terminal so the scenarios have someone to protect:

```bash
uv run agi-agent add-manager --name "Shashank" --telegram-id 1001
```

Go to **Try an impostor** and run the scenarios. With `FAKE_LLM=true` the model is an offline echo, so this costs nothing. Start again from an empty database any time with `docker compose down -v && docker compose up -d && uv run alembic upgrade head`.

## Connect your real channels

**Telegram**

1. Message `@BotFather`, create a bot, and put its token in `TELEGRAM_BOT_TOKEN`.
2. Run `uv run agi-agent telegram-poll` (polling needs no public URL).
3. Message your bot. It answers as a stranger, and the dashboard's **People** page shows your numeric Telegram ID under *Unlinked accounts*.
4. Add yourself with that ID: `uv run agi-agent add-manager --name "Shashank" --telegram-id <your id>`.

**Slack**

1. Create an app at [api.slack.com/apps](https://api.slack.com/apps) from `slack-manifest.yml`.
2. Generate an app-level token with `connections:write` (`SLACK_APP_TOKEN`), install the app, and copy the bot token (`SLACK_BOT_TOKEN`). Put your workspace's team ID (starts with `T`) in `SLACK_TEAM_ID`.
3. Run `uv run agi-agent slack-socket`.
4. Link Slack to yourself the way a real user would: DM Ava "it's Shashank". The code arrives on Telegram. Type it in Slack and the two channels become one person.

## Real Claude calls and the US$10 cap

1. Create a workspace in the [Claude Console](https://platform.claude.com/dashboard), make an API key, and set the workspace spend limit to US$10 as a second guard.
2. In `.env`: `ANTHROPIC_API_KEY=...`, `FAKE_LLM=false`, `MAX_SPEND_USD=10`.
3. `uv run agi-agent hello` makes one tiny Haiku 5.5 call (a small fraction of a cent) to check the key. `uv run agi-agent spend` shows the total.

Replies to known people use Sonnet 5.5. My estimate is about a cent per reply with the current prompts, so US$10 covers several hundred to a thousand messages. When the cap is reached, Ava says it is paused and makes no further calls. Prices live in `src/agi_agent/brain/pricing.py`; a model missing from that table is refused.

## Commands

| Command | What it does |
| --- | --- |
| `uv run agi-agent serve` | Webhooks and the dashboard API, on 127.0.0.1:8000 |
| `uv run agi-agent telegram-poll` | Receive Telegram messages without a public URL |
| `uv run agi-agent slack-socket` | Receive Slack messages over Socket Mode |
| `uv run agi-agent add-manager` / `add-colleague` | Register a person and their first channels |
| `uv run agi-agent unlink --identity <id>` | Undo a link |
| `uv run agi-agent freeze` / `unfreeze` | Kill switch |
| `uv run agi-agent spend` | Spend against the cap |
| `uv run agi-agent hello` | One tiny real Claude call |

## How the code is laid out

```text
src/agi_agent/
  pipeline.py        verify → resolve → audience gate → think → policy → reply
  channels/          Telegram and Slack adapters, signature checks
  identity/          resolver, linking codes, trust tiers
  context/           the audience gate (SQL filter)
  memory/            episodic log and audience-tagged memories
  brain/             Claude client, pricing, spend ledger, prompt and reply schema
  policy/            action rules, kill switch
  api/               admin API for the dashboard
  db/                SQLAlchemy models (migrations in alembic/)
skills/charter.md    who the agent is and its hard limits (cached in every prompt)
tests/redteam/       the five impostor scenarios
web/                 React + Vite dashboard
```

## Safety notes

- The admin API listens on 127.0.0.1 only and needs `ADMIN_TOKEN`.
- `ENABLE_SIMULATOR=true` lets the dashboard inject messages as any account. Keep it off anywhere but your laptop.
- `.env` holds your keys and is git-ignored. Never paste keys into chats or prompts.
- Tell colleagues what Ava remembers and why before you add them (Singapore PDPA notice).

## What comes next (phase 2)

Memory tiers (episodes, time-valid facts, a person model, skills), the correction loop (draft, your edit, a proposed lesson, your approval), the prediction log, the nightly sleep job with rollback, and the first measured gates from Infant to Child.
