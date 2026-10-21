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
