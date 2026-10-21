# Overnight build log (2026-10-21 → 22)

Shashank asked Claude Code to keep building overnight. Each item lands only when the
backend tests, lint, types, the frontend checks and the full browser suite pass.

| # | Item | Status | Commit |
|---|---|---|---|
| 1 | Background job runner | done | c869c61 |
| 2 | Per-day / per-month spend limits by purpose | done | 1de580d |
| 3 | Message log: channel message and thread ids, no duplicate redeliveries | done | (see git log) |
| 4 | Reminders and follow-ups (first real skill) | in progress | |
| 5 | Tool loop + web research with sources | todo | |
| 6 | Citations on every answer | todo | |
| 7 | Keyword recall over the notebook (instead of newest 30) | todo | |
| 8 | Skills framework skeleton (manifest, consent, Skills page; Calendar/Gmail connectors on mocks) | todo | |

## Needs Shashank in the morning
- Create a Google Cloud OAuth client for Calendar/Gmail (instructions will be here).
- Decide on embeddings for meaning-based search (local model ~1 GB, or a paid API).

## Notes
- Research report 2 saved: docs/research/Ava skills tools and cited memory.md. Key finding: the chat turn uses strict JSON output, which Claude can't combine with citations, so tools + citations (items 5–6) need the chat turn rewritten as a tool loop (the report's sprint 1).
- Skill protocol (Shashank, 21 Oct night): one uniform, industry-standard contract every skill must follow: Agent Skills `SKILL.md` for instructions + MCP (with its OAuth 2.1 auth spec) for tools and connectors + a thin `superaide.json` manifest only for what those lack (version, publisher, permissions, connectors, settings, signature), with a JSON Schema and a conformance validator. Written up as `docs/decisions/0006-skill-protocol.md` in item 8.
