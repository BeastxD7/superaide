# Architecture

Superaide has three parts: a Next.js frontend, a FastAPI backend that serves only JSON, and Postgres. Chat channels (Telegram, Slack) talk to the backend directly. The backend calls Claude for language and keeps every decision about identity, access and actions in code.

```text
 Browser ── HTTPS ──► Next.js (:3000) ── /api/* forwarded ──┐
                       pages, SSR                            ▼
 Telegram ── webhook or long polling ──►  FastAPI backend ──► Claude API
 Slack ───── Socket Mode or Events API ─►  (one process)        (spend-capped)
                                                 │
                                                 ▼
                                    Postgres 17 + pgvector
```

## Request paths

**The app.** The browser talks only to the Next.js server. Public pages (marketplace, agent profiles) are rendered there with data fetched from the API, and private pages are rendered in the browser. `proxy.ts` redirects signed-out visitors away from private pages. API calls go to `/api/*` on the same origin. A route handler forwards them to FastAPI, passing cookies through and dropping client-supplied `X-Forwarded-For` unless `TRUST_PROXY_HEADERS=true`. The frontend signs in with `/api/auth/*` and gets an httpOnly session cookie for its own origin. App calls under `/app/*` resolve the signed-in account, then the agent hire, then the owner's `Person`. Every state-changing request must carry `X-Requested-With`; together with the SameSite=Lax cookie and a CORS policy limited to `FRONTEND_ORIGIN`, that blocks cross-site request forgery.

**A chat message** (from any channel, including the app's own chat) goes through `pipeline.py`:

1. **Verify.** A webhook secret or Slack signature is checked, or the message is dropped.
2. **Resolve.** `identity/resolver.py` maps the platform's immutable ID to a `ChannelIdentity` and its `Person`. An unknown sender gets stranger mode: a fixed reply, no memory and no model call.
3. **Audience gate.** `context/audience.py` works out who can see this conversation. Memories are filtered in SQL to items that everyone present may see, so private items never reach the prompt.
4. **Think.** `brain/turn.py` builds the prompt: the cached charter, the owner brief (`knowledge/profile.py`, scoped to the audience), visible memories and recent events. `brain/llm.py` calls Claude after `brain/budget.py` reserves the worst-case cost.
5. **Policy.** The model can only propose actions. `policy/actions.py` decides from the database (`manager`, `colleague`), never from what a message claims.
6. **Reply** through the channel's messenger, and log the event.

## Data

| Area | Tables | Notes |
| --- | --- | --- |
| Accounts | `user_account`, `user_session`, `auth_event`, `agent_hire` | Session tokens are stored only as SHA-256 hashes. One active hire per agent. |
| Identity | `person`, `channel_identity`, `link_code`, `identity_link_event` | Trust tiers; every link can be undone. |
| Conversations | `context`, `episode_event` | Episodic log per conversation. |
| Knowledge | `memory_item`, `document`, `contact`, `owner_preference` | Facts carry category, source, document, confidence, visibility and a validity window. |
| Operations | `spend_entry`, `audit_event`, `integration_secret`, `onboarding` | Secrets are AES-256-GCM ciphertext with the slot as associated data. |

Migrations live in `backend/alembic/versions/`. CI checks that they match the models (`alembic check`) and that they roll back.

## Where knowledge comes from

- **The owner, in the app:** introduction answers become typed settings (`owner_preference`, `contact`) and notebook facts with source `chat` or `call`.
- **Files and links:** `knowledge/extract.py` reads PDF, DOCX and text files and fetches web pages with SSRF guards. GitHub profiles use the GitHub API; LinkedIn and X are refused with guidance. `knowledge/ingest.py` stores the document, then asks Claude Haiku for structured facts (no transaction stays open during the call), then links each fact to its document. A file shared before any AI is connected stays `stored`. `knowledge/pending.py` has one hook, `brain_became_ready()`, that queues a per-instance `read_pending` job; it runs after Claude or a provider is connected, a model is chosen, or credits are switched on, and the worker's sweep (at start-up and every 10 minutes) covers anything else, such as the operator's own key. Ava tells the owner in chat when each document has been read.
- **Other people:** messages are logged as episodes. Today, third parties can't write facts about the owner.

## Credentials

`vault/` keeps connected credentials. The master key comes from `SECRETS_MASTER_KEY`, else the OS keychain, else a key file readable only by the user. Each credential is checked with its service before it is saved, and only a hint is shown back. When credentials change, the agent and the channel listeners are rebuilt in place.

## API responses

Every error is one envelope (`status`, `status_code`, `message`, `data`, `error{code, detail, request_id}`) built in `backend/src/superaide/envelope.py`, with an `X-Request-ID` on every response. See ADR 0013.

## Decisions

Significant choices are recorded in [decisions/](decisions/).
