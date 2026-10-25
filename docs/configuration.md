# Configuration

Every setting is an environment variable. This page is the readable reference; [`backend/.env.example`](../backend/.env.example) is the source of truth (it lists every variable once, with the accepted values and a longer comment) and its top is an index of every true/false switch. The table below is generated from that file by `scripts/gen_configuration_doc.py`, and `make check-env` fails when a setting exists in the code but is missing from the example file, its feature-flags index, or this page.

Decided and tested features are **on by default**. A flag is only for risky, costly or experimental work and defaults to off. A feature that needs an external key turns on when the key is present.

## Set up

1. **Backend:** `cp backend/.env.example backend/.env` (`start.command` does this for you and fills in `ADMIN_TOKEN`). The file lives next to the code the API runs from, `backend/`. Never commit it; it is git-ignored.
2. **Frontend** (only if you change anything): `frontend/.env.example` to `frontend/.env.local`. Variables starting `NEXT_PUBLIC_` are baked in at build time, so rebuild after changing them. The installable app (`NEXT_PUBLIC_PWA=1`) is opt-in locally and on by default in the production compose file.
3. **Restart** the API (and the worker, if you run one) after every change. Environment variables win over `backend/.env`; keys connected in the app (Workspace, Connections) win over both.
4. A line `# NAME=value` is commented out and shows the built-in default: uncomment it only to change it.
5. `SECRETS_MASTER_KEY`, `SECRETS_MASTER_KEY_FILE` and `SUPERAIDE_KEY_FILE` are read from the process environment only, not from `backend/.env`. On a Mac leave them unset (the Keychain holds the key).
6. **Production** uses `.env.prod.example` at the repository root, see [Production differences](#production-differences).

## Variables by area

Long comments are shortened here; the full text is next to the variable in `backend/.env.example`. "Same note as the entry above" means the variable belongs to the group commented above it.

### Database

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://agent:agent@localhost:5432/agent` | yes | Postgres 17 with pgvector (docker compose up -d db). Secret (contains a password). Default shown. |
| `MIGRATION_DATABASE_URL` | empty | yes | Schema owner for migrations and `superaide db-app-role`, so DATABASE_URL can be the app's own role (no superuser, no BYPASSRLS) and Row-Level Security applies (ADR 0009, deploy/README.md). Secret. Default empty = use DATABASE_URL. |
| `TEST_DATABASE_URL` | `postgresql+psycopg://agent:agent@localhost:5432/agent_test` |  | Postgres used by the test suite only. The tests WIPE it, so it must differ from DATABASE_URL (conftest refuses otherwise). Read by tests and `make test`, not by the app. Default shown. |
| `EVAL_DATABASE_URL` | empty |  | Postgres used by `python -m superaide.evals` (every scenario wipes it, so never your real database). Evals only. Default: a database named agent_eval on localhost. |
| `DISPOSABLE_DB_MARKERS` | `test,eval,scratch,e2e` |  | Destructive helpers (the eval harness, `reset-db`, the test suite's wipes) refuse any database whose name lacks one of these markers or that equals the DATABASE_URL in backend/.env. Comma-separated, case-insensitive. Default shown. |
| `LIVE_DATABASE_URL` | empty | yes | Optional: one more URL that must never be wiped (for example production, when it is not in this file). Secret. |
| `DB_POOL_SIZE` | `10` |  | Connection pool per process (API, worker). At most (size + overflow) // 2 - 1 chat turns run at once, because a turn holds a connection and the spend guard takes a second. Postgres max_connections (100 by default) must cover size + overflow for every process that connects. Integers >= 1 (overflow >= 0); timeout in seconds. Defaults shown. |
| `DB_MAX_OVERFLOW` | `20` |  | Same note as the entry above. |
| `DB_POOL_TIMEOUT` | `30` |  | Same note as the entry above. |

### Deployment mode and tenancy

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `DEPLOY_MODE` | `self_hosted` |  | self_hosted: local model servers (Ollama, vLLM) on this machine or network are allowed. hosted: many owners on shared servers; no owner-typed address may reach private, loopback, link-local or metadata ranges. self_hosted \| hosted. |
| `ALLOW_UNSAFE_PUBLIC_SELF_HOSTED` | `false` |  | true: run a self-hosted server with open sign-up and this server's own keys on a public address anyway. Leave false: the startup check refuses that combination because sign-ups would spend your keys. Default false. |
| `TENANT_ID` | `00000000-0000-4000-8000-000000000001` |  | Fixed ids every row carries so the project can go multi-tenant later. Do not change on an existing database. |
| `AGENT_ID` | `00000000-0000-4000-8000-000000000002` |  | Same note as the entry above. |
| `AGENT_NAME` | `Ava` |  | The name the agent introduces itself with. Default Ava. |
| `RUN_CHANNELS_IN_API` | `true` |  | Run the Telegram and Slack listeners inside the API process (follow connections made in the app). true \| false. Default true. Production compose sets false on the api and runs them in the worker. |
| `TYPING_INDICATORS` | `true` |  | Show that Ava is working while she prepares a reply: Telegram "typing..." (refreshed every ~4 seconds until the reply is posted); on Slack the assistant-thread status where the app has it, otherwise a "Working on it..." message that the reply then answers as a new message (so it notifies) and deletes the placeholder. Only for people Ava answers with the model; it changes neither what she says nor who may see it. true \| false. Default true. |
| `RUN_JOBS_IN_API` | `true` |  | Run background jobs (reminders, follow-ups, digests) inside the API process. true \| false. Default true. Same note. |
| `REMINDER_AUTO_CLOSE_DAYS` | `7` |  | Days a delivered reminder stays open before it is closed for the owner (marked done, audited). 0 = never. Default 7. |
| `THIRD_PARTY_MESSAGE_RETENTION_DAYS` | `0` |  | Empty the text of third parties' messages older than this many days. 0 = never (deleting by age is the owner's call). Default 0. |

### Claude API and spend limits

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | empty | yes | Anthropic API key. Secret. Blank = offline echo replies, nothing spent (owners can also connect their own key in the app). |
| `FAKE_LLM` | `false` |  | true forces the scripted offline model even when a key is set. true \| false. Default false. |
| `MODEL_CHAT` | `claude-haiku-5-5` |  | Models by role. A bare id runs on Claude; "<preset>::<model>" runs on a provider the owner connected in the app. A model missing from the price table (brain/pricing.py) is refused, so add its prices when you change these. |
| `MODEL_TRIAGE` | `claude-haiku-5-5` |  | Same note as the entry above. |
| `MODEL_DEEP` | `claude-opus-5-5` |  | Same note as the entry above. |
| `MAX_SPEND_USD` | `10` |  | Hard ceiling on total Claude API spend for this build, in US dollars (number). Default 10. |
| `BUDGET_DAILY_USD` | `0.50` |  | Rolling limits inside the lifetime cap, in US dollars (0 turns one off): any 24 hours, any 30 days. Defaults shown. |
| `BUDGET_MONTHLY_USD` | `5.00` |  | Same note as the entry above. |
| `BUDGET_DAILY_BY_BUCKET` | `{"chat": 0.40, "dissent": 0.10, "fork": 0.15, "rules": 0.02, "memory": 0.15, "research": 0.25, "triage": 0.05, "voice": 0.30, "receptionist": 0.10, "learning": 0.10}` |  | Per kind of work, any 24 hours, as JSON {"bucket": usd}. Default (all ten buckets) shown; a bucket you leave out has no limit of its own (the daily and monthly limits still apply), so copy the whole object when you change one. |
| `BUDGET_MONTHLY_BY_BUCKET` | `{"voice_call": 5.00}` |  | A live voice call (#125) has its own monthly bucket, `voice_call`, inside MAX_SPEND_USD and every limit above (the tightest wins). JSON {"bucket": usd}, any 30 days. Default shown; {} removes the voice cap only. |
| `VOICE_CALL_MAX_SECONDS` | `600` |  | Longest one live call may run, in seconds of caller audio heard (10 to 3600). Default 600. |
| `PLATFORM_DAILY_CAP_USD` | `0` |  | These limits apply to each hired Ava separately. Calls paid with Superaide's own model key (credits, below) also share one platform-wide allowance per UTC day, in US dollars; 0 refuses them all. Default 0. |
| `PLATFORM_AI_ENABLED` | `false` |  | --- Superaide AI credits (optional; costs you money, so an owner decision) --- Owners may use the platform's own Claude key instead of bringing theirs, only after they opt in on the AI model page, and their own key always comes first. true \| false. Default false. |
| `PLATFORM_ANTHROPIC_API_KEY` | empty | yes | The platform's Claude key (else ANTHROPIC_API_KEY is used). Secret. Never shown, logged or stored in an owner's vault. |
| `PLATFORM_MODEL` | `claude-haiku-5-5` |  | Model every role runs on when credits pay. Default claude-haiku-5-5. |
| `PLATFORM_OWNER_DAILY_USD` | `0.50` |  | Each owner's share of the credits (rolling 24 hours / 30 days, US dollars), inside PLATFORM_DAILY_CAP_USD. Defaults shown. |
| `PLATFORM_OWNER_MONTHLY_USD` | `5.00` |  | Same note as the entry above. |
| `PLATFORM_KEY_OWNER_EMAILS` | empty |  | Accounts (comma-separated emails) whose own Ava uses ANTHROPIC_API_KEY as its own key even when hosted: the operator's. Normal per-Ava budget, outside the credits. Emails are not verified, so list only accounts that already exist. Default empty. |
| `WEB_TOOLS_ENABLED` | `true` |  | --- Web search and reading --- Let Ava search and read the web for the owner (about US$0.01 a search, from the research budget). true \| false. Default true. |
| `BRAVE_SEARCH_API_KEY` | empty | yes | Brave Search API key, for models without built-in search (Claude has its own). Secret. Default empty. |
| `WEB_SEARCH_USD_PER_CALL` | `0.01` |  | What one search costs on your plan in US dollars, charged against the cap before each search. Default 0.01. |
| `EMBEDDER` | `local` |  | --- Embeddings for recall --- local: on this machine, free, about 220 MB model downloaded once. voyage: Voyage AI's API, so private facts leave the machine (needs VOYAGE_API_KEY). fake: tests. none: no embeddings. Default local. |
| `EMBED_MODEL_LOCAL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |  | Local model name (50+ languages). Default shown. |
| `EMBED_CACHE_DIR` | empty |  | Where the local model is kept. Default empty = ~/.cache/superaide/models. |
| `VOYAGE_API_KEY` | empty | yes | Voyage AI key. Secret. Only used when EMBEDDER=voyage. |
| `EMBED_MODEL_VOYAGE` | `voyage-4-lite` |  | Same note as the entry above. |
| `EMBED_MONTHLY_USD` | `1.00` |  | Cap on Voyage spend in any 30 days, US dollars (local costs nothing). Default 1.00. |

### Telegram

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | empty | yes | Bot token. Secret. Blank = no bot from .env. |
| `TELEGRAM_WEBHOOK_SECRET` | empty | yes | Webhook mode only: any random string of letters, digits, _ and -. Secret. |
| `TELEGRAM_WEBHOOK_BASE_URL` | empty |  | Public https address of this server (for example https://ava.example.com). When set, each owner's bot gets a webhook at <this>/webhooks/telegram/<endpoint> with its own secret instead of polling (ADR 0009). Default empty = polling. |

### Slack

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `SLACK_BOT_TOKEN` | empty | yes | Bot token (xoxb-...). Secret. |
| `SLACK_APP_TOKEN` | empty | yes | App-level token (xapp-...) for Socket Mode. Secret. |
| `SLACK_SIGNING_SECRET` | empty | yes | Signing secret that verifies Slack requests. Secret. |
| `SLACK_TEAM_ID` | empty |  | Your workspace's team ID (starts with T). Anyone from another team is treated as external. Not secret. |

### Google

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `GOOGLE_CLIENT_ID` | empty |  | In the Google Cloud project enable BOTH the Google Calendar API and the Gmail API, and under Data access add the calendar.readonly, calendar.events, gmail.readonly and gmail.compose scopes. Redirect URIs to register on the client: <FRONTEND_ORIGIN>/api/auth/google/callback (sign-in) and <FRONTEND_ORIGIN>/api/app/connections/google/callback (Calendar). |
| `GOOGLE_CLIENT_SECRET` | empty | yes | Secret. |
| `GOOGLE_SIGNIN_ENABLED` | `false` |  | "Continue with Google" on log-in and sign-up. Needs the client id and secret. true \| false. Default false (off: /auth/google/* answer 404). |
| `GOOGLE_CALENDAR_PUSH` | `false` |  | Calendar change notifications (events.watch) so meeting prep notices new meetings at once. Needs a public https PUBLIC_BASE_URL and the /webhooks/* route open. true \| false. Default false (the 30-minute scan runs either way). |
| `PUBLIC_BASE_URL` | empty |  | Public https address of this API as Google and other services reach it (for example https://ava.example.com). Also used for links in emails (before FRONTEND_ORIGIN). Default empty = TELEGRAM_WEBHOOK_BASE_URL. |
| `GOOGLE_REDIRECT_ORIGINS` | empty |  | Extra origins registered with Google as authorised redirect URIs besides FRONTEND_ORIGIN (comma-separated exact origins, no wildcards; each must also be allowed by LINK_ORIGINS or DEV_ORIGINS), for example http://127.0.0.1:3000. Default empty. |
| `GOOGLE_REFRESH_TOKEN` | empty | yes | Calendar refresh token for a self-hosted server. Secret. Normally leave blank: each owner signs in from Connections and the token is kept in their vault. |
| `GMAIL_REFRESH_TOKEN` | empty | yes | Gmail refresh token, same rules. Secret. |
| `GOOGLE_FAKE_BASE_URL` | empty |  | End-to-end tests only: a fake Google at this address replaces accounts.google.com, and only while ENABLE_SIMULATOR is on. Never set on a real server. Default empty. |

### Operator console, origins and API surface

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `ADMIN_TOKEN` | empty | yes | Bearer token for /api/* and the operator console at /console. Secret. Make one with: python3 -c "import secrets; print(secrets.token_urlsafe(24))"   (start.command fills this in when blank) |
| `FRONTEND_ORIGIN` | `http://localhost:3000` |  | The browser origin the API accepts (CORS) and builds links from. Default http://localhost:3000. |
| `LINK_ORIGINS` | empty |  | Other addresses people may open the app at, for links in emails (comma-separated, * allowed in the host), for example https://*.trycloudflare.com (a phone or tunnel). Links use the address the person opened the app at when listed here, else PUBLIC_BASE_URL, else FRONTEND_ORIGIN. Default empty. |
| `DEV_ORIGINS` | empty |  | Host names (comma-separated) that count like LINK_ORIGINS; start.command sets this for a phone on your Wi-Fi. Default empty. |
| `ENABLE_SIMULATOR` | `false` |  | Development only: lets the operator console (/console, Simulator page) inject pretend messages to try impostor scenarios. true \| false. Default false. For local testing uncomment it and set true, then restart the API; never on a public server (production compose forces false). |
| `ENABLE_API_DOCS` | `true` |  | The interactive API reference (/docs, /redoc, /openapi.json). true \| false. Default true; production compose forces false. |

### Credentials vault

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `SECRETS_MASTER_KEY` | `# openssl rand -base64 32` | yes | On a Mac the key lives in the Keychain, elsewhere in a key file, so you can leave these blank. On a server set one of them (deploy/README.md). Secret. Back it up: without it a database backup cannot decrypt saved keys. NOTE: these three are read from the process environment only, NOT from this file. Export them in the shell or service that starts the API (docker-compose.prod.yml passes SECRETS_MASTER_KEY through for you). |
| `SECRETS_MASTER_KEY_FILE` | empty |  | or a file holding that value, for example /run/secrets/vault_master_key |
| `SUPERAIDE_KEY_FILE` | empty |  | Where the master key file is kept when no Keychain or env value is used. Default ~/.config/superaide/master.key. |

### Accounts and sessions

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `SESSION_COOKIE_NAME` | `superaide_session` |  | Session cookie name. The frontend must use the same value (frontend/.env.example). Default superaide_session. |
| `SESSION_COOKIE_SECURE` | `false` |  | true when served over HTTPS (the cookie is then never sent over plain http). true \| false. Default false. Production compose forces true. |
| `SESSION_DAYS` | `30` |  | Sliding session length in days, counted from the last use. Default 30. |
| `LOGIN_MAX_FAILURES` | `5` |  | Wrong passwords per account before it locks, and for how many minutes. Defaults 5 and 15. |
| `LOGIN_LOCK_MINUTES` | `15` |  | Same note as the entry above. |
| `LOGIN_FAILURES_PER_IP` | `20` |  | Failed log-ins per address per 15 minutes, and sign-ups per address per hour. Defaults 20 and 10. |
| `SIGNUPS_PER_IP_PER_HOUR` | `10` |  | Same note as the entry above. |
| `ALLOW_MANY_OWNERS` | `true` |  | Anyone who signs up can hire their own Ava with its own memory, people, connections and spend (ADR 0009). false = single-owner mode: the first account to hire Ava is its only owner. true \| false. Default true. |
| `REQUIRE_VERIFIED_EMAIL` | `false` |  | Hiring needs a confirmed email address. Turn on once email below works. true \| false. Default false. |
| `VERIFY_LINK_HOURS` | `48` |  | How long a verification link and a password-reset link stay valid (hours, minutes), and how many such emails one account may request per hour. Defaults 48, 30 and 5. |
| `RESET_LINK_MINUTES` | `30` |  | Same note as the entry above. |
| `EMAIL_LINKS_PER_ACCOUNT_PER_HOUR` | `5` |  | Same note as the entry above. |
| `LINK_CODE_TTL_MINUTES` | `10` |  | --- Identity linking (adding Telegram or Slack to a person) --- One-time code lifetime in minutes, wrong tries per code, codes per day per platform identity, codes per day per person (across all accounts claiming to be them). Defaults 10, 5, 3, 6. |
| `LINK_CODE_MAX_ATTEMPTS` | `5` |  | Same note as the entry above. |
| `LINK_CHALLENGES_PER_IDENTITY_PER_DAY` | `3` |  | Same note as the entry above. |
| `LINK_CHALLENGES_PER_PERSON_PER_DAY` | `6` |  | Same note as the entry above. |
| `LINK_RECONFIRM_AFTER_DAYS` | `30` |  | A linked Telegram/Slack account silent this many days must be confirmed again in the app. 0 = never. Default 30. |

### Email

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `MAIL_BACKEND` | `log` |  | log: writes each email, live links included, to the API log (development only). smtp: sends through any SMTP server. |
| `MAIL_LOG_BODY` | `true` |  | With MAIL_BACKEND=log: true writes the whole email, live verification and reset links included, to the API log; false logs only the recipient and subject (the end-to-end suite uses false). true \| false. Default true. |
| `MAIL_FROM` | `Superaide <no-reply@localhost>` |  | The From header. Default "Superaide <no-reply@localhost>". |
| `SMTP_HOST` | empty |  | SMTP server for MAIL_BACKEND=smtp. Default empty. |
| `SMTP_PORT` | `587` |  | Same note as the entry above. |
| `SMTP_USERNAME` | empty |  | SMTP login. Not needed for an open relay. Default empty. |
| `SMTP_PASSWORD` | empty | yes | Secret. |
| `SMTP_SECURITY` | `starttls` |  | starttls \| ssl \| none. Default starttls. |
| `SMTP_TIMEOUT_SECONDS` | `15` |  | Seconds to wait for the server. Default 15. |

### Triage, Gmail and escalation

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `TRIAGE_ENABLED` | `false` |  | Record a handle/hold/interrupt/defer/verify decision for every message from someone other than the owner and escalate interrupts until acknowledged. true \| false. Default false. |
| `TRIAGE_KNN_ENABLED` | `false` |  | Deletable kNN triage head (#117), SHADOW ONLY: records a score from your own feedback beside each decision and compares it with your corrections. By itself it decides nothing; "Needs you" shows "Shadow: agrees with you 86% over 40 corrections, needs 200". Promotion is yours: when the head clears z 1.96, at least 200 corrections in 30 days and a lower bound of 0.8, "Needs you" shows a Promote button (it ends by itself after 14 days under the gate, and you can take it back). Needs TRIAGE_ENABLED. true \| false. Default false. |
| `TRIAGE_KNN_RAISE_ENABLED` | `false` |  | Raise-only head (#117): after you press Promote, a message the model called calm and no rule silenced may be raised to the interrupt path when your past corrections say similar messages were urgent. It never lowers or silences anything; the stage cap, quiet hours and the interrupt budget still apply. Needs TRIAGE_ENABLED and TRIAGE_KNN_ENABLED. true \| false. Default false. |
| `MIRROR_ENABLED` | `false` |  | "The Mirror": a weekly note of up to three patterns in the owner's own reminders and messages, each with receipts. The patterns are counted in code and worded from templates, so it makes no model call. 404 while off; each owner must also opt in. true \| false. Default false. |
| `GMAIL_TRIAGE_ENABLED` | `false` |  | Poll each owner's Gmail inbox (history.list) and triage new mail. Needs TRIAGE_ENABLED and the Google client above. Off: Gmail is read only when the owner asks in chat. true \| false. Default false. |
| `CONNECTOR_FACTS_ENABLED` | `false` |  | Short facts from the owner's calendar and verified mail: after a calendar read or a Gmail triage, a small-model call may keep up to 3 short facts per item ("dentist moved to Friday 3pm") as owner-only notes with the item id as provenance. Never the raw text; never newsletters, payment, legal or credential mail; mail facts are third-party claims (hearsay under the Echo Filter). Disconnecting Google or removing the capability deletes them. At most CONNECTOR_FACTS_PER_DAY a day, inside the spend cap. true \| false. Default false. |
| `CONNECTOR_FACTS_PER_DAY` | `20` |  | Same note as the entry above. |
| `GMAIL_POLL_MINUTES` | `5` |  | Minutes between inbox polls. Default 5. |
| `OVERNIGHT_TUNER_ENABLED` | `false` |  | Overnight Tuner (#118): /app/tuner proposes a better triage prompt; nothing changes until the owner approves. true \| false. Default false. |

### Honesty, memory and safety features

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `SECOND_OPINION_ENABLED` | `false` |  | Second opinion (#123): a memory-free second view on the owner's own claims or choices (up to two small-model calls per judged message); above AGREEMENT_LIMIT the reply carries the strongest case for the other side. true \| false. Default false. |
| `AGREEMENT_LIMIT` | `0.75` |  | Agreement rate (0 to 1) above which the other side is argued. Default 0.75. |
| `RELATIONSHIP_SAFETY_ENABLED` | `false` |  | Relationship safety (#124): fixed human-written replies for heavy or "do you love me?" owner messages, no claims of feelings, a reach-out-to-a-person rule. All in code. true \| false. Default false. |
| `SKILL_RATCHET_ENABLED` | `false` |  | Skill Ratchet (#120): /app/playbooks; Ava drafts playbooks, only owner-edited ones go live, at most 50 active. true \| false. Default false. |
| `RUBBER_STAMP_BRAKE_ENABLED` | `false` |  | Rubber-Stamp Brake (#134): when recent approvals are all yes and faster than a card can be read, high-risk cards need a typed reason. Only adds friction. true \| false. Default false. |
| `ECHO_FILTER_ENABLED` | `false` |  | Echo Filter (#137): facts from a link, Telegram or Slack are stored as hearsay with their origin until a second independent origin or the owner confirms. true \| false. Default false. |
| `ONE_GOOD_QUESTION_ENABLED` | `false` |  | One Good Question (#121): /app/question; at most one clarifying question a day. true \| false. Default false. |
| `TASK_PROTOCOL_ENABLED` | `true` |  | Human-like task protocol (#330): for an errand that ends in an approval Ava reads the mail/calendar/notes first and records a plan (goal, evidence, route, ability check, recommendation) that code validates; without one propose_action is refused. true \| false. Default true (decided and tested, so it ships on; false turns it off). |
| `IDENTITY_CLAIM_REVIEW` | `false` |  | Identity claim review (#93): an unknown account whose display name, or claimed name, exactly matches (normalised) a known person is treated as a stranger, never as them. The owner is asked in the app (Needs you): "Someone is asking about you and says they are <name>. Do you know them?" with what the platform told us, and only the owner's answer links an account. /app/identity-claims is 404 while off. true \| false. Default false. |
| `CLAIM_QUESTIONS_PER_DAY` | `5` |  | Most owner pushes about such claims per day, all senders together (the app lists every claim regardless). Default 5. |
| `WHY_REPLAY_ENABLED` | `false` |  | "Why did you say that?" (#122): /app/why/*; each request costs up to REPLAY_MAX_ABLATIONS + 1 small-model calls inside the cap. true \| false. Default false. |
| `REPLAY_MAX_ABLATIONS` | `8` |  | Most ablation calls one replay may make. Default 8. |
| `OWNER_TWIN_ENABLED` | `false` |  | Owner Twin (#119): /app/twin; a calibration page scoring a predictor of the owner's approve/reject choices (reads approvals, grants nothing). true \| false. Default false. |
| `REHEARSAL_ENABLED` | `false` |  | Rehearsal room (#58): practise a hard conversation with Ava playing the other side; nothing is sent to anyone and the session is deleted after a day. /app/rehearsal/*. true \| false. Default false. |
| `SCHOOL_ENABLED` | `false` |  | Ava School (#57), local half: write generic lessons, consent to each, export to a file or import one. No network sharing. /app/school/*. true \| false. Default false. |
| `SCHOOL_SHARING_ENABLED` | `false` |  | Ava School sharing (#57): exports become signed files (Ed25519, key derived from the vault master key) holding only lessons whose category (role) the owner allowed (default none); imports must carry a valid signature and still wait for the owner. Needs SCHOOL_ENABLED. true \| false. Default false. |
| `SCHOOL_PLAYBOOK_ENABLED` | `false` |  | Ava School playbook (#57): playbook text per lesson, an eval per lesson, and a lesson in the owner's prompt only after the eval passed and the owner switched it on. Needs SCHOOL_ENABLED. true \| false. Default false. |
| `SCHOOL_SIGNER` | `local` |  | Who signs school files. "local" is the built-in Ed25519 signer; another provider is added in school/signing.py. Default local. |
| `SLEEP_JOB_ENABLED` | `false` |  | Nightly sleep job (#50): groups the day's conversations into episodes and tidies notes (exact duplicates; with a model also near-duplicates and contradictions; at most a quarter a night, pre-image saved first). true \| false. Default false. |
| `SLEEP_JOB_HOUR` | `3` |  | Local hour (0 to 23, in the owner's time zone) the sleep job runs. Default 3. |
| `MEMORY_RECHECK_ENABLED` | `false` |  | Spaced "is this still true?" (#49): the digest may carry a few lasting facts to re-confirm, never as an interruption. true \| false. Default false. |
| `MEMORY_RECHECK_PER_WEEK` | `2` |  | Most such questions per week. Default 2. |
| `IMPORT_LEASE_ENABLED` | `false` |  | Import lease (#133): /app/import-leases/*; bring in context cards converted on the owner's machine from a third-party export, under a time-limited lease that erases them. true \| false. Default false. |
| `AIRLOCK_ENABLED` | `true` |  | Airlock (#128, code half): other people's contact details (e-mail, phone, handles, cards, IBANs, ids, addresses) and the names of everyone Ava knows (not the owner's own) become placeholders before a model sees the conversation, and the reply, streamed text included, gets the originals back. It is a pattern pass in code, no model and no network, tested on English, Hindi and Spanish conversations (see tests/privacy/test_airlock_fixtures.py); a name nobody listed and text inside images are not found. Costs a few milliseconds a call. true \| false. Default true; set false to send conversations unchanged. |
| `VOICE_PROVIDER` | `auto` |  | Live voice (#125, #448): there is no on/off flag. Voice is on when a key is present (VOICE_PROVIDER_API_KEY, or DEEPGRAM_API_KEY / the Deepgram key saved under Connections) and off, with a hint in the app saying what to add, when there is none. An action heard on a call becomes an approval marked voice-origin that only a tap inside the Superaide app can approve (a Telegram or Slack tap cannot). auto (default: deepgram when a key is present, else none) \| none (force off) \| fake (replays scripted transcripts, offline, for tests and the demo) \| deepgram (the hosted adapter in docs/voice.md, turn by turn) \| realtime (the adapter stub for a real vendor; refuses to run until an adapter is... |
| `VOICE_PROVIDER_API_KEY` | empty | yes | The realtime vendor's API key (Secret). Normally saved under Connections (the vault) instead of here. |

### Approvals, accountability and guardrails

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `APPROVAL_REQUIRE_EXECUTOR` | `true` |  | A kind of approval nothing can carry out (message_someone_else, publish_post, ...) or a route nothing can use (a web form, a contact page) is refused when Ava proposes it: no card is created and she tells the owner so (#326). true \| false. Default true. |
| `STEPUP_MODE` | `confirm` |  | How a sensitive change (letting Ava read one-time codes, ...) is confirmed (#451). confirm (default): a dialog with a warning and "I understand", two steps on the server (a single-use confirmation, two minutes, tied to the session). password: the dialog plus the account password, for accounts that have one (Google-only accounts keep the dialog). |
| `APPROVAL_BINDING` | `strict` |  | Bind an approval to the content the owner saw (#111). off: not checked. record: a mismatch is audited but the send goes ahead. strict: a mismatch is not sent, and neither is an approval that has no recorded hash (one made before the check existed): it is not run and the owner is told to ask Ava again, never silently let through. Default strict. |
| `APPROVAL_TTL_HOURS` | `0` |  | Hours before an unanswered approval request can no longer be approved. 0 = never expires. Default 0 (an owner's call). |
| `TRACE_RECORDER` | `true` |  | Every tool call recorded as hashes only (#110). true \| false. Default true. |
| `AUDITION_ENABLED` | `false` |  | Audition (#110): compare 2 or 3 candidate skills side by side on the owner's last tasks. Instructions and tool choices only: every effect is stubbed and nothing touches the network. Small-model calls inside the spend cap. true \| false. Default false. |
| `AUDITION_MAX_TASKS` | `20` |  | How many of the most recent tasks one audition replays (1 to 50). Default 20. |
| `MODEL_DRILL_ENABLED` | `false` |  | Model-swap drill (#110): replay the owner's last tasks on two models (effects stubbed, no network) and show where the call sequences differ. Small-model calls on both models, inside the spend cap. true \| false. Default false. |
| `DRILL_MAX_TASKS` | `20` |  | How many of the most recent tasks one drill replays on each model (1 to 50). Default 20. |
| `REPLAY_BEFORE_UPDATE_ENABLED` | `false` |  | Replay before update (#110): before a skill, prompt or model change goes live, replay the last tasks with and without it (effects stubbed, local reads only, small-model calls inside the spend cap) and show how the calls differ by name and argument hash; the owner approves or rejects. true \| false. Default false. |
| `REPLAY_MAX_TASKS` | `10` |  | How many of the most recent tasks one preview replays (1 to 50). Default 10. |
| `ACTION_LOG` | `false` |  | Hash-chained log of tool calls and executed approvals (#113). true \| false. Default false. |
| `SURFACE_CHECK_ON_BOOT` | `false` |  | Refuse to start when a protected file differs from security/surface.lock.json (#114). true \| false. Default false. |
| `MANDATES_ENABLED` | `true` |  | Mandate cards (#112): a signed, narrowing token lets Ava do a named, limited thing without asking each time; never covers send or spend. true \| false. Default true (#448); no mandate exists until the owner signs one, so nothing is skipped. |
| `MANDATE_SHADOW_ENABLED` | `true` |  | Mandate shadow check (#112): needs MANDATES_ENABLED. Each approval records whether a mandate would have covered it; the owner is still asked every time. Default true (#448): the clock for MANDATE_SKIP_MIN_DAYS starts on a fresh install. true \| false. |
| `MANDATE_SKIP_ENABLED` | `true` |  | Mandate skip (#112): needs MANDATES_ENABLED and MANDATE_SHADOW_ENABLED. A covering signed mandate lets Ava add or move an event with no guests (Child stage and above) without a card, after MANDATE_SKIP_MIN_DAYS of shadow data; never on a turn that read untrusted content, never at the Infant stage. Every skip is audited and shown to the owner. While on, an event with no guests asks at Infant and Child like any write_external tool (owner-accepted, #448). Default true; it only acts once the owner signs a covering mandate. true \| false. |
| `MANDATE_SKIP_MIN_DAYS` | `14` |  | Days the shadow check must have been recording before a mandate may skip a card. 0 to 365. Default 14. |
| `MCP_PINNING` | `false` |  | MCP pinning (#116): an MCP tool whose description, schema or annotations differ from what the owner approved is blocked. true \| false. Default false (no MCP client calls it yet). |
| `SKILL_COOLDOWN_ENABLED` | `false` |  | Skill cooldown (#129): a recorded skill release reaches installs only after a wait set by its effect class (read 0 h, write_own 24 h, write_external/send/spend 72 h). A reviewer-signed security patch is the only bypass; names of retired skills are reserved for good. true \| false. Default false. |
| `SKILL_REVIEWER_KEY` | empty | yes | Secret key that signs a reviewed security patch (HMAC-SHA256 of "<skill id>@<version>"). Blank means nothing can bypass the cooldown. Keep it out of the repo. |
| `SKILL_SIGNALS_ENABLED` | `false` |  | Skill trust signals (#130): every skill version is read by several independent scanners and routed by the pattern of their verdicts (none alone can promote); the runtime counts calls, errors, reverts, effect classes and host names (no arguments, no content) into a behaviour card, shown only above SKILL_CARD_MIN_COHORT installs; a skill whose behaviour leaves its manifest is demoted and not offered to Ava. true \| false. Default false. |
| `SKILL_CARD_MIN_COHORT` | `5` |  | Fewest installs before a behaviour card is shown (at least 2). Default 5. |
| `SKILL_RECEIPTS_ENABLED` | `false` |  | Skill receipt log (#131): every decision on a skill's action writes a receipt with no content into an append-only hash chain; a skill's tier is computed from receipts by backend/scripts/skill_tier.py (standard library only), which anyone can run on an export. true \| false. Default false. |
| `SKILL_RECEIPT_MIN_DAYS` | `3` |  | Days between an owner's first and last decision on a skill before their receipts count. Default 3. |
| `SKILL_RECEIPT_OWNER_CAP` | `20` |  | Most receipts of one owner that count per skill version. Default 20. |
| `PAYOUTS_ENABLED` | `false` |  | Creator payout ledger (#132): lines in whole minor units for outcomes the owner accepted unedited (after a floor, not in a batch, up to a daily cap), a clawback for a revert inside the window, and a refund bond for send/spend skills. It moves NO money; the only settlement provider is "null". true \| false. Default false. |
| `PAYOUT_PROVIDER` | `null` |  | Settlement provider. Only "null" exists (it pays nothing); a real one is the owner's decision. Default null. |
| `PAYOUT_CURRENCY` | `USD` |  | Three-letter currency code written on ledger lines. Default USD. |
| `PAYOUT_PER_OUTCOME_MINOR` | `50` |  | Gross amount per accepted outcome, in minor units (cents). Default 50. |
| `PAYOUT_FEE_BPS` | `2000` |  | Platform fee in basis points of the gross amount (2000 = 20%). Default 2000. |
| `PAYOUT_FLOOR_SECONDS` | `10` |  | An approval faster than this many seconds does not count. Default 10. |
| `PAYOUT_CLAWBACK_DAYS` | `7` |  | Days after an accrual in which a revert takes it back, and before it is payable. Default 7. |
| `PAYOUT_BOND_MINOR` | `5000` |  | Refund bond a creator of a send or spend skill must hold to earn, in minor units. Default 5000. |
| `PAYOUT_DAILY_OWNER_CAP` | `10` |  | Most approvals from one owner per skill per day that earn a payout. Default 10. |
| `CIRCUIT_BREAKER_ENABLED` | `false` |  | Circuit breaker (#140): more than CIRCUIT_BREAKER_LIMIT deletes or sends inside CIRCUIT_BREAKER_WINDOW_SECONDS freeze that owner's Ava (the kill switch), and the owner's message that is exactly HALT_WORD freezes her before any model sees it. true \| false. Default false. |
| `CIRCUIT_BREAKER_LIMIT` | `5` |  | Same note as the entry above. |
| `CIRCUIT_BREAKER_WINDOW_SECONDS` | `60` |  | Same note as the entry above. |
| `HALT_WORD` | `halt` |  | Same note as the entry above. |
| `FOOTPRINT_GUARD_ENABLED` | `false` |  | Footprint Guard (#136): creating something in the world under the owner's name (account, profile, public post, pull request, review) always needs approval and is logged; an outbound message naming someone outside the owner's contacts is flagged. true \| false. Default false. |
| `PRINCIPAL_CARD_ENABLED` | `true` |  | Principal Card (#135): every email Ava sends is signed "Regards, Ava, personal assistant of <first name>" with an honest AI line and a link to a signed public page. The owner can switch it off in Settings or untick it per email. Global kill flag. true \| false. Default true. |
| `PRINCIPAL_CARD_DAYS` | `30` |  | Days the principal card page stays valid (1 to 365). Default 30. |
| `CARD_MISSES_PER_MINUTE` | `30` |  | Missed card links (unknown, expired, revoked) one address may make a minute before the card page answers 429. 0 = no limit. Default 30. |
| `QUOTE_ODDS_ENABLED` | `false` |  | Quote and Odds (#139): approval cards show honest odds from logged outcomes; at QUOTE_MIN_SAMPLES outcomes with a success rate below QUOTE_MIN_SUCCESS, approving prepares the action instead of running it. true \| false. Default false. |
| `QUOTE_MIN_SAMPLES` | `5` |  | Same note as the entry above. |
| `QUOTE_MIN_SUCCESS` | `0.5` |  | Same note as the entry above. |

### Memory socket, dissent, fork night, time-travel rules

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `MEMORY_SOCKET_ENABLED` | `false` |  | Memory Socket (#138): read-only MCP endpoint POST /mcp/memory for the owner's other assistants (one token per consumer, a scope the owner picks, audience-filtered in SQL, rate limited, access log). true \| false. Default false. |
| `MEMORY_SOCKET_PER_MINUTE` | `30` |  | Requests per minute per consumer. Default 30. |
| `BRAIN_SHARE_ENABLED` | `false` |  | Glass brain share link (#55): the owner can create an unlisted link (a long random token, stored hashed, shown once) to a counts-only picture of Ava's memory (topic words and numbers, no names or note text). Expires in at most 7 days, revocable any time. It makes a page reachable without an account, so it stays off until you decide. true \| false. |
| `BRAIN_SHARE_PER_MINUTE` | `30` |  | Visitors per link per minute. Default 30. |
| `DISSENT_DESK_ENABLED` | `false` |  | Dissent Desk (#126): three DIFFERENT models answer a high-stakes choice independently; code decides if a minority view is shown. Costs more (bucket "dissent"). true \| false. Default false. |
| `DISSENT_MODELS` | empty |  | Comma-separated model ids the provider layer can route. Required for the desk to run. Default empty. |
| `DISSENT_MIN_STAKES` | `3` |  | Only choices at or above this stake (1 to 5) run. Default 3. |
| `FORK_NIGHT_ENABLED` | `false` |  | Fork Night (#127): overnight, three read-only drafts of a choice the owner left (no tools, no outside content; bucket "fork"). true \| false. Default false. |
| `FORK_NIGHT_HOUR` | `2` |  | Local hour (0 to 23) they run. Default 2. |
| `TIME_TRAVEL_RULES_ENABLED` | `false` |  | Time-travel rules (#52): a standing order in plain words is compiled to a deterministic rule, replayed on the last TIME_TRAVEL_REPLAY_DAYS of the owner's own messages, run in shadow first, promoted after TIME_TRAVEL_PROMOTE_AFTER agreeing firings. true \| false. Default false. |
| `TIME_TRAVEL_REPLAY_DAYS` | `90` |  | Same note as the entry above. |
| `TIME_TRAVEL_PROMOTE_AFTER` | `20` |  | Same note as the entry above. |
| `TIME_TRAVEL_MIN_AGREEMENT` | `0.9` |  | Same note as the entry above. |

### "Ask my Ava" public intake link

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `ASK_LINK_ENABLED` | `false` |  | /a/<handle> page where anyone can leave a request (no model call for the visitor). Off: /p/ask/* and /app/ask-link/* answer 404. |
| `ASK_FORM_SECRET` | empty | yes | Signs the public form's token. Secret. Blank: a random key per process (fine for one API process; a restart only reloads the form). |
| `ASK_MIN_SECONDS` | `3` |  | A form sent faster than this many seconds is a bot and is quietly dropped. Default 3. |
| `ASK_TOKEN_MAX_AGE_S` | `7200` |  | Form token lifetime in seconds. Default 7200. |
| `ASK_PER_IP_PER_HOUR` | `5` |  | Submissions per address per hour; per handle per hour; per handle per day. Defaults 5, 20, 100. |
| `ASK_PER_HANDLE_PER_HOUR` | `20` |  | Same note as the entry above. |
| `ASK_PER_HANDLE_PER_DAY` | `100` |  | Same note as the entry above. |
| `ASK_HANDLE_TRIES_PER_DAY` | `20` |  | How many new handles one owner may try to claim in a day (a "taken" answer would otherwise list handles). Default 20. |

### Voice

| Variable | Default | Secret | What it does |
| --- | --- | --- | --- |
| `VOICE_INTRO_V2` | `false` |  | Off: the original video call. On: hands-free turn-taking (voice detection in the browser), Claude interviewing, a natural voice and a "what I learned" list the owner confirms before anything is saved. Free without keys (the browser's own speech). true \| false. Default false. |
| `VOICE_ASR` | `auto` |  | Speech-to-text: auto (the owner's own key, then Superaide credits, then the browser) \| deepgram \| browser \| fake (tests). |
| `VOICE_TTS` | `auto` |  | Text-to-speech: auto \| elevenlabs \| deepgram \| browser \| fake (tests). |
| `VOICE_MAX_TURN_SECONDS` | `60` |  | A longer answer than this many seconds is refused before it is transcribed. Default 60. |
| `DEEPGRAM_API_KEY` | empty | yes | Self-hosted only: used when no key was saved in Connections. Deepgram (speech-to-text, English Aura voices): https://console.deepgram.com. ElevenLabs (natural voices): https://elevenlabs.io/app/settings/api-keys. Both Secret. |
| `ELEVENLABS_API_KEY` | empty |  | Same note as the entry above. |
| `PLATFORM_DEEPGRAM_API_KEY` | empty | yes | Superaide AI credits only: the platform's own voice keys, for owners who opted in. Server environment only. Both Secret. |
| `PLATFORM_ELEVENLABS_API_KEY` | empty |  | Same note as the entry above. |
| `PUSH_ENABLED` | `false` |  | Web push to the owner's installed app. On: the first use makes one VAPID key pair for this deployment (encrypted in platform_state). /app/push/* answers 404 when off. true \| false. Default false. |
| `VAPID_SUBJECT` | `mailto:admin@localhost` |  | Who push services contact about this sender: use a real address in production. Default mailto:admin@localhost. |
| `PUSH_PAD_BYTES` | `1024` |  | Every encrypted push is padded to this many plaintext bytes so its size reveals nothing. Default 1024. |
| `PUSH_DEFAULT_PREVIEW` | `none` |  | What a lock screen shows for a new device: none ("Ava / Needs you") \| sender \| reason. Default none. |
| `PUSH_TTL_SECONDS` | `180` |  | Seconds a push stays useful, and retries for 429 and 5xx answers inside it. Defaults 180 and 3. |
| `PUSH_MAX_ATTEMPTS` | `3` |  | Same note as the entry above. |
| `PAGINATION_CURSOR_MAX_AGE_S` | `86400` |  | List pagination (ADR 0014): seconds a "next page" cursor stays valid. Default 86400 (24 hours). |

## How to enable

**Impostor simulator (local testing only).** Off by default. To try impostor scenarios from the operator console, put `ENABLE_SIMULATOR=true` in `backend/.env` (uncomment the line in the example), restart the API, then open `/console` with your `ADMIN_TOKEN` and use the Simulator page. Never turn it on on a public server.

**Models and keys.** Set `ANTHROPIC_API_KEY` (or let each owner connect their own in Workspace, Connections) and Ava replies for real; without a key she runs offline and spends nothing. `MODEL_CHAT`, `MODEL_TRIAGE`, `MODEL_DEEP` choose models; a model missing from `brain/pricing.py` is refused. Spend is capped by `MAX_SPEND_USD` (lifetime), `BUDGET_DAILY_USD`, `BUDGET_MONTHLY_USD` and `BUDGET_DAILY_BY_BUCKET`, per Ava. Other providers (Ollama, OpenAI-compatible, and so on) are connected in the app, then used as `<preset>::<model>`; on `DEPLOY_MODE=hosted` owner-typed private addresses are refused.

**Google sign-in, Calendar and Gmail.** One OAuth client of type "Web application" serves all three.
1. In Google Cloud enable the Google Calendar API and the Gmail API, and add the scopes `calendar.readonly`, `calendar.events`, `gmail.readonly`, `gmail.compose`.
2. Register redirect URIs `<FRONTEND_ORIGIN>/api/auth/google/callback` (sign-in) and `<FRONTEND_ORIGIN>/api/app/connections/google/callback` (Calendar). Other origins you use go in `GOOGLE_REDIRECT_ORIGINS` and must also be allowed by `LINK_ORIGINS` or `DEV_ORIGINS`.
3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, then `GOOGLE_SIGNIN_ENABLED=true` for sign-in. Calendar and Gmail need no flag for chat use: the owner clicks Connect Google in Connections and the token goes to their vault.
4. Gmail triage: also `TRIAGE_ENABLED=true` and `GMAIL_TRIAGE_ENABLED=true`. Calendar push: `PUBLIC_BASE_URL=https://...` with `/webhooks/*` routed to the API, then `GOOGLE_CALENDAR_PUSH=true`.

**Telegram.** Create a bot with @BotFather and connect the token in the app, or set `TELEGRAM_BOT_TOKEN`. Polling needs nothing else. For webhooks set `TELEGRAM_WEBHOOK_BASE_URL` (or `PUBLIC_BASE_URL`) to your https address and route `/webhooks/*`; `TELEGRAM_WEBHOOK_SECRET` is for the single env-configured bot.

**Slack.** Create the app from [`slack_manifest.yml`](../backend/src/superaide/channels/slack/manifest.yml) (Socket Mode), then connect it in the app or set `SLACK_BOT_TOKEN` (`xoxb-`), `SLACK_APP_TOKEN` (`xapp-`), `SLACK_SIGNING_SECRET` and `SLACK_TEAM_ID` (people from other workspaces count as external). The bot needs these scopes: `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `im:write`, `users:read`, and `channels:history` + `groups:history` so Ava reads the thread when you @-mention her in one (always on, never stored). At startup and when you connect, Ava checks the granted scopes: a missing one is logged as an ERROR and shown on Connections and in a banner, and only the part that needs it stops (a thread is then answered without being read). Add the scope under OAuth & Permissions and reinstall the app.

**Email (SMTP).** `MAIL_BACKEND=smtp`, `MAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SECURITY` (`starttls` / `ssl` / `none`). Then `REQUIRE_VERIFIED_EMAIL=true` if hiring should need a confirmed address. `MAIL_BACKEND=log` (the default) writes each email, **including live verification and password-reset links**, to the API log: fine on your own machine, never on a shared or public server.

**Many owners or one.** `ALLOW_MANY_OWNERS=true` (default) lets anyone who signs up hire their own isolated Ava. `false` makes the first account to hire Ava the only owner. On a public server also keep `DEPLOY_MODE=hosted` so sign-ups never run on your keys (the compose file forces this).

**Voice and push.** `VOICE_INTRO_V2=true` for the conversational introduction call (free with the browser's speech). Add `DEEPGRAM_API_KEY` and/or `ELEVENLABS_API_KEY` for better voices, or let owners save theirs. `PUSH_ENABLED=true` for web push (use a real `VAPID_SUBJECT`).

**Switches that are not booleans.** `APPROVAL_BINDING` (`off` / `record` / `strict`, default `strict`: a send whose content changed after the owner saw it is blocked), `APPROVAL_TTL_HOURS` (0 = approvals never expire), `STEPUP_MODE` (`confirm` / `password`), `EMBEDDER` (`local` / `voyage` / `fake` / `none`; `voyage` sends private facts to Voyage AI), `DEPLOY_MODE` (`self_hosted` / `hosted`), `MAIL_BACKEND` (`log` / `smtp`), `VOICE_PROVIDER`, `VOICE_ASR` and `VOICE_TTS`, `THIRD_PARTY_MESSAGE_RETENTION_DAYS` (0 = keep forever).

**Which flags are safe.** Safe to switch on yourself: bounded cost, no data leaves your server, nothing irreversible. Owner decision: it spends money, sends data to another company, makes something public, or widens what Ava may do without asking. The grouping is in the index at the top of `backend/.env.example` ("Safe to turn on by yourself" and "Needs an owner decision first"). Features whose routes are gated answer 404 while off.

## Production differences

`.env.prod.example` (copied to `.env` next to `docker-compose.prod.yml`, or filled in by `deploy/bootstrap.sh`) is a shorter file for the container stack ([deploy/README.md](../deploy/README.md)):

- Required: `DOMAIN`, `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `SECRETS_MASTER_KEY` (back it up; without it saved keys cannot be decrypted) and `ADMIN_TOKEN`. The compose file refuses to start without them.
- Fixed by the compose file whatever `.env` says: `DATABASE_URL` (the app's own role, so Row-Level Security applies), `MIGRATION_DATABASE_URL` (empty), `FRONTEND_ORIGIN` (`https://DOMAIN`, or `PUBLIC_URL`), `SESSION_COOKIE_SECURE=true`, `DEPLOY_MODE=hosted`, `ENABLE_SIMULATOR=false`, `ENABLE_API_DOCS=false`, and `RUN_JOBS_IN_API` / `RUN_CHANNELS_IN_API` `false` (a separate worker runs them).
- Only in this file: `HTTP_PUBLISH`, `HTTPS_PUBLISH` (host ports for Caddy) and `PUBLIC_URL` (when the public address is not `https://DOMAIN`).
- It lists only the common settings. Every other variable in `backend/.env.example`, including all the feature flags above, can be added to the same `.env` and is passed to the api and worker.
- Keys in this file are never used for an owner on `hosted`: owners connect their own model, Telegram and Slack in the app. The exceptions are `PLATFORM_KEY_OWNER_EMAILS` (the operator's own Ava) and Superaide AI credits.
- Web container variables (`API_INTERNAL_URL`, `TRUST_PROXY_HEADERS`) are set by the compose file; the full frontend list is in `frontend/.env.example`.

