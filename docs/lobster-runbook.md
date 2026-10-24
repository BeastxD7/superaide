# Lobster Rescue: volunteer runbook

For the owner, to run the five-volunteer measurement in #141. Everything below runs on the volunteer's own machine. Nothing is sent by the tools; the volunteer reads each output and decides what to send back.

## Who to ask

Five people who have run OpenClaw or Hermes: ideally three you know and two from the community. Each says yes in their own words before anything is run. Tell them plainly what comes back: counts and yes/no answers, never files, names or text.

## What the volunteer runs

```bash
cd backend
uv run superaide lobster-dry-run ~/path/to/workspace   # what an import WOULD do; writes nothing
uv run superaide lobster-kit ~/path/to/workspace       # the anonymous result; asks before saving
uv run superaide lobster-layout ~/path/to/workspace    # folder shape, unknown names hidden
```

1. `lobster-dry-run` shows how many memory cards would be made, how many lines would be left out (a line that gives orders or holds a secret), and the skills, which are only listed, never imported or run. It writes nothing and calls no model or network.
2. `lobster-kit` prints the whole anonymous result and saves it only on a yes. Send the `lobster-result-<id>.json` file back.
3. `lobster-layout` is only needed for a Hermes workspace. It prints paths with every name hidden except a fixed list of well-known ones (`MEMORY.md`, `skills`, `SKILL.md`, ...). `--names` shows every name; the volunteer reads it first and removes anything private before sending.

## What the owner runs

```bash
uv run superaide lobster-kit-summary result1.json result2.json ...
```

## Layouts the tools know

| Layout | Detected by | Memory read from |
| --- | --- | --- |
| OpenClaw | `openclaw.json`, or `MEMORY.md` or `memory/` at the top | `MEMORY.md`, `USER.md`, `DREAMS.md`, `memory/*.md` |
| Hermes | a `memories/` folder, or `config.yaml` with `SOUL.md` | `memories/MEMORY.md`, `memories/USER.md` (entries separated by a section sign) |

The Hermes shape comes from its public documentation and community write-ups (a `~/.hermes` home with `config.yaml`, `.env`, `auth.json`, `SOUL.md`, `memories/`, `skills/<name>/SKILL.md` with `scripts/` and `references/`, and a session store). The test fixtures are built from those descriptions. They are NOT verified against a real install. A volunteer's `lobster-layout` output is what verifies or corrects them; until then treat Hermes support as best effort.

## What is never imported

Skills (listed with whether they ask for a shell and whether they carry scripts, never read as memory and never run), the persona file `SOUL.md`, the session store, `.env`, `auth.json` and any config. Installing an imported skill is a separate decision that belongs behind the skills-store review (#129), not the importer.

## What still needs people

Recruiting and consent. The measurement is complete when five result files exist.
