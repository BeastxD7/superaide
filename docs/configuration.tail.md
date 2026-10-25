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

