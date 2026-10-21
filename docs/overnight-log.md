# Overnight build log (2026-10-21 → 22)

Shashank asked Claude Code to keep building overnight. Each item lands only when the
backend tests, lint, types, the frontend checks and the full browser suite pass.

| # | Item | Status | Commit |
|---|---|---|---|
| 1 | Background job runner | done | c869c61 |
| 2 | Per-day / per-month spend limits by purpose | done | 1de580d |
| 3 | Message log: channel message and thread ids, no duplicate redeliveries | done | (see git log) |
| 4 | Reminders and follow-ups (first real skill) | done | f48acfc |
| 5 | Tool loop + web research with sources | done | f48acfc |
| 6 | Citations on every answer | done (verifier/abstain check still to do) | f48acfc |
| 7 | Keyword recall over the notebook (instead of newest 30) | done | f48acfc |
| 9 | Learning from conversation (owner's messages → notebook facts, after the reply) | done | see git log |
| 10 | Daily briefing: today's reminders, dates from notes (birthdays, anniversaries) with what Ava knows about the person, pending approvals; nothing sent when there's nothing to say | done | see git log |
| 11 | Google Calendar skill (OAuth 2.1 + PKCE sign-in, encrypted refresh token, calendar_events tool with citations) | committed locally, NOT pushed: 1 failing test (tests/api/test_authz.py::test_the_owner_can_read_every_owner_page — likely the new /google/callback GET route, which redirects instead of 200); no frontend card yet | WIP |
| 8 | Skills framework (protocol ADR 0006, manifest + JSON Schema validator, installed skills, consent, Skills page) | done (Calendar/Gmail connectors next) | see git log |

## Needs Shashank in the morning
- Usage limit reached ~01:30; work stopped. Next: fix the authz test for the Google callback route, add a Google card on the Connections page, then push.
- Create a Google Cloud OAuth client for Calendar/Gmail (instructions will be here).
- Decide on embeddings for meaning-based search (local model ~1 GB, or a paid API).

## Notes
- Ava now learns from what you tell her in chat (01:10). A live check with Haiku 5.5 showed three problems, now fixed in the extraction prompt:
  - guessed details ("Priya, a colleague") are no longer added;
  - relative dates are written as absolute ones ("from November 2026", not "next month");
  - to-dos are skipped (they aren't facts).
  It costs about US$0.0005 a message and only learns from the owner's own messages, not other people's, to avoid memory poisoning.
- Testing changed at Shashank's request (00:40): fast backend tests + type checks before each push; GitHub CI runs the full browser suite.
- Live experiment with real Claude Haiku 5.5 (00:45, about $0.013 total):
  - The tool loop works end to end: Haiku called `recall` twice in parallel, and our `search_result` sources came back as native citations with our `ava://` ids (two steps cost $0.0005).
  - Web search works on Haiku with the basic `web_search_20250305` tool: one search plus a cited answer cost $0.012. Searches are the expensive part.
  - Found and fixed: Haiku sometimes repeats the quoted source as its own cited block, so answers said each fact twice; the quote is now folded into the [n] marker.
  - Found and fixed: forecasts came in °F; Ava now uses metric units and the owner's time zone.
- Research report 2 saved: docs/research/Ava skills tools and cited memory.md. Key finding: the chat turn uses strict JSON output, which Claude can't combine with citations, so tools + citations (items 5–6) need the chat turn rewritten as a tool loop (the report's sprint 1).
- Skill protocol (Shashank, 21 Oct night): one uniform, industry-standard contract every skill must follow: Agent Skills `SKILL.md` for instructions + MCP (with its OAuth 2.1 auth spec) for tools and connectors + a thin `superaide.json` manifest only for what those lack (version, publisher, permissions, connectors, settings, signature), with a JSON Schema and a conformance validator. Written up as `docs/decisions/0006-skill-protocol.md` in item 8.

## Night of 2026-10-22 (coordinator: Claude Code)

- 02:00 IST: dev checkout fast-forwarded to main; dev DB migrated 0009 → 0012.
- E2E in Chrome with the owner's account. "plz remind me on telegrm to drnk watr in 3 mins" → Ava reasoned, called create_reminder, and saved "Drink water" with channel=telegram (typo-free, correct time).
  - App delivery: OK.
  - Telegram delivery FAILED: the saved bot token is rejected by Telegram (getMe 401; polling refused too).
  - Connections still showed "Connected" and the failure was silent. A channel-health agent is assigned to surface it, tell the owner and stop Ava promising Telegram.
  - **Owner action:** paste a fresh BotFather token under Connections → Telegram.
- ADR 0009 (many owners, one Ava) approved. T0, T9, T10-now and T12 are building; the rest wait on 0007/0008.
- Innovation research is in `docs/research/AGI era growth ideas.md`; top ideas for the morning are listed there (proposals only).
- 02:45 IST: Telegram root cause found. Telegram's automated abuse system (12:48 AM) reset sessions, reset bot tokens and froze @AvaXD_bot, which was 11 minutes old. It's not our bug. **Owner action:** check Devices, enable 2FA, then appeal (`/appealbot…` in the Abuse Notifications chat) or create a new bot and paste its token.
- Cross-channel memory (ADR 0008) is complete on main (6bab999). The dev DB is at 0015 (providers, tenancy T0/T9, embeddings) and the dev stack was restarted.
- Live E2E: "what color do i hate?" in the app → "You dislike black. You said so on Telegram earlier… [1]" (US spelling + paraphrase + cross-channel + citation). FIXED.
- The AI model page (/dashboard/models) works live; the providers agent will add real brand logos to the cards.
- 02:20 IST: the owner linked a new bot (@Ava2xD_bot). Its first message (/start) was silently dropped as a "duplicate": Telegram numbers messages per chat *per bot*, so the new bot's #1 matched the old bot's #1. Fixed in 647af96 (message ids include the bot's public id; regression tests).
- Live Telegram E2E via Telegram Web in Brave works: replies, a reminder delivered on Telegram, cross-channel recall "You dislike black… [1]".
- Polish bugs assigned to a channel formatting agent: raw **Markdown** on Telegram, and "[1]" with no source list on chat channels.
- 02:32 IST heartbeat: 8 agents busy (QA, providers follow-ups, tenancy T0–T12-now, identity re-link security, channel health, Telegram formatting, deps, reliability).
  - Providers (ADR 0007) landed: the AI model page, OpenAI-compatible adapter, prices, hosted-mode SSRF guard, DNS-rebinding fix.
  - New non-cancelling "Full" CI workflow is running.
  - Dev spend tonight: US$0.049 (64 calls); lifetime dev US$0.07.
- 03:20 IST:
  - Providers follow-ups landed: the DNS-rebinding guard on every connection, stream marker cleanup, the Brave Web search card, official price tables (sources and dates in code), and a key per endpoint (0016).
  - Channel health landed (Token rejected → Connections warning, in-app note, Ava stops promising the channel, 5-minute poller backoff).
  - Telegram/Slack formatting landed (77fed86).
  - The reliability scorecard landed (91a9eca): 93% mean, 87% pass^3 over 62 live scenarios, US$0.34. The same agent is fixing its top 10 and will re-run.
  - Cloudflare quick tunnel opened for the owner's phone (dev only; random URL).
  - The owner's account email is "shashank@pebbleroad.cmo" (typo); they're resetting the password via the CLI. Ask in the morning whether to correct it to .com.
- 03:05 IST:
  - Backed up the dev DB (~/superaide-dev-2026-10-22-0258.dump) and migrated it to 0017 (RLS policies; no behaviour change while the app connects as the superuser). **The owner's step** of creating the restricted `superaide_app` role (runbook in ADR 0009 T1 / the tenancy agent's report) waits for approval.
  - The dev stack restarted after a startup race; live Telegram check OK.
  - Two new issues were assigned to the reliability agent: delivered reminders still listed as "open", and Ava doubting the verified owner on Telegram.
  - ADR 0010 (triage & escalation) proposed: decision rules → small classifier → policy, a stage-gated ladder with Acknowledge buttons, a feedback loop that makes the trust stages real. 10 open questions for the owner.
- 03:35 IST heartbeat:
  - Landed: identity links bound to the confirming bot/app (0a9ae99), the QA job-worker fix (06edd08) and the gitleaks PR fix.
  - The Full CI run is in progress; the previous Full run failed, and QA is fixing forward.
  - Agents: QA, identity (finishing), tenancy T6, reliability fixes, triage foundations (flag off).
  - The dev checkout stays at its current version until the identity report arrives and Full is green.
- 03:45 IST: identity binding (0a9ae99) landed. Dev migrated to 0018; the owner's Telegram link is backfilled to the current bot (@Ava2xD_bot) and the dev stack restarted cleanly.
  - Live Telegram check OK: owner recognised; the reply is formatted (no raw **), with a "Sources: [1] Note · preference · from Telegram" footer.
  - Identity follow-ups in progress: restrict confirm/unlink to the owner's own chat identities; don't let unconfirmed bots refresh last-seen.
- 04:10 IST:
  - Reliability fixes 1–12 landed (38b306b..dab423f). Live scorecard: 93% → 97% mean, 87% → 94% pass^3; citations, approvals and briefing now at 100%. Cost US$0.10.
  - Per-instance budget plus the platform allowance landed (ADR 0009 T6, 38b306b, migration 0019). The platform cap defaults to $0 until the owner decides pricing.
  - Identity follow-ups landed (aa54a50).
  - Next: reliability round 3 (double-typo recall, the remaining honesty hedge, ~20 harder scenarios); tenancy T2 (accounts → own Ava); triage foundations; mobile ADR.
