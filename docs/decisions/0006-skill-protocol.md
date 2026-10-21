# 0006: One skill protocol, built from industry standards

Status: accepted (2026-10-22)

## Context

Superaide's value comes from what Ava can *do*, and Shashank wants a marketplace where official
and community skills add abilities to a personal assistant ("manage my Instagram", "triage my
Gmail"). He asked for one uniform protocol that every skill must follow, and that is compatible
with the industry's standards rather than a format of our own. The research in
`docs/research/Ava skills tools and cited memory.md` shows two standards already cover most of
it, and what they leave out.

## Decision

A Superaide skill is a folder with three parts, and nothing else is a skill:

```text
my-skill/
  SKILL.md         instructions — the open Agent Skills standard (agentskills.io), unchanged
  superaide.json   the Superaide manifest — only what the standards don't cover
  ...              optional resources the SKILL.md refers to
```

1. **`SKILL.md` follows the Agent Skills standard exactly** (frontmatter `name`, `description`,
   optional `license`, `compatibility`, `metadata`). A skill written for Claude, OpenClaw or any
   other Agent Skills product drops in unchanged, and ours work elsewhere.
2. **Tools use MCP's tool shape** (`name`, `description`, `inputSchema` as JSON Schema). A tool is
   either built into Superaide (official skills) or served by an MCP server the manifest names.
   Connecting to accounts follows the MCP authorization spec (OAuth 2.1 with PKCE, audience-bound
   tokens, no token passthrough). Tokens live in Superaide's encrypted vault and never reach the
   model.
3. **`superaide.json` adds only what the standards lack**, and the runtime — not the prompt —
   enforces it:
   - identity: `id` (`publisher/name`), `version` (semver), `publisher`, `tier` (`official` | `community`);
   - `tools[]`: each with an **effect class** (`read_public`, `read_private`, `write_own`,
     `write_external`, `send`, `spend`), which decides whether it runs, asks the owner, or is
     refused at the agent's current trust stage;
   - `connectors[]`: provider and OAuth scopes;
   - `network`: the hosts it may reach; `budget`: what it may spend;
   - `settings`: a JSON Schema for per-owner settings.

   Field names mirror MCP's `server.json` and Claude's plugin `marketplace.json` so they are
   familiar to anyone who has built for either.

The JSON Schema for `superaide.json` is published at `backend/src/superaide/skills/manifest.schema.json`
and every skill must validate against it before it can be listed or installed.

## Rules every skill obeys

- **Permissions are what the manifest says, and only that.** The consent screen shows them; an
  update that adds a permission or connector scope asks again (as Chrome does).
- **Side effects go through approvals.** `write_external`, `send` and `spend` tools end in an
  approval card at the Infant and Child stages, whatever the skill's instructions say.
- **Community skills start as instructions only** (no code runs on our servers); their tools come
  from MCP servers named in the manifest, and they may not use `send` or `spend` until reviewed.
- **Untrusted content stays data.** Results from web pages, emails or community tools mark the
  turn as tainted; tainted turns can't take side effects without an explicit approval.

## Consequences

- Built-in abilities (memory, reminders, web research) are re-packaged as official skills, so the
  same install, consent and enforcement path applies to everything.
- Signing, review and revocation for a public marketplace come later; the manifest already has
  the fields they need.
