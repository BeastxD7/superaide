# Lobster Rescue measurement kit

The five-volunteer experiment from #141: how many OpenClaw or Hermes workspaces have a secret lying in a plain file, a gateway reachable beyond the machine, or a skill that asks for a shell.

Everything runs on the volunteer's machine. The kit reads the folder, imports nothing, runs nothing and sends nothing.

## For a volunteer

```bash
cd backend
uv run superaide lobster-kit ~/path/to/your/workspace
```

The kit prints the whole result and asks before saving it. The result holds counts and yes/no answers only. It has no folder name, file name, line, text, secret value or machine name, and the volunteer id is random. Open the saved `lobster-result-<id>.json`, check it, and send it to the project owner if you are happy with it. Declining saves nothing.

Use `--out FILE` to choose where it is saved and `--yes` to skip the question.

## For the owner

```bash
uv run superaide lobster-kit-summary result1.json result2.json ... [--json]
```

Prints how many of the workspaces had each exposure. A file that is not a `lobster-kit/1` result is refused.

## What is not covered

Only the documented plain markdown names are read (`MEMORY.md`, `USER.md`, `DREAMS.md`, `memory/*.md`). The Hermes file layout is unverified. Recruiting the volunteers is a human step.
