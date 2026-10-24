# Example names

The project owner's real name is never used as an example, fixture, demo or placeholder name. Examples use
clearly fictional people instead.

| Where | Persona | First name | Email |
| --- | --- | --- | --- |
| Backend (tests, evals, seed-demo, CLI, source comments), `scripts/`, `.github/` | Jordan Whitfield | Jordan | `jordan@example.com` |
| `e2e/`, `frontend/`, `docs/` | Ananya Menon | Ananya | `ananya@example.com` |

Other fictional people stay as they are (Priya Tan, Meera Iyer, Rahul Verma, Priya Nair, Dana, Sam, ...).
Inside one layer the name is the same everywhere, and a test never uses a different name from the data it asserts.
Derived forms follow the mapping: `Shashank Rao` -> `Jordan Whitfield`, `shashank-rao` -> `jordan-whitfield`,
`Shashank K` -> `Jordan W`, `U_DEMO_SHASHANK` -> `U_DEMO_JORDAN`, `shashank@x` -> `jordan@x`.

## What is allowed to keep the old name

Only facts about the real person (the owner's notes, `CLAUDE.md`, the overnight and morning logs, the package
author field, ADR sentences quoting the owner's report) and this file. The list is
`scripts/example_names_allowlist.txt`: one path per line, or `path::regex` to allow only matching lines.

## The script

```bash
python3 scripts/rename_example_names.py          # rewrite tracked files in place (idempotent, stdlib only)
python3 scripts/rename_example_names.py --check  # exit 1 and list any disallowed occurrence
```

`backend/tests/guards/test_example_names.py` runs the check, so CI fails when the old name comes back.

## Rebasing an open branch after the rename landed

If a conflict touches a line that still has the old name (or your new code uses it):

```bash
git checkout --theirs <file>          # during a rebase "theirs" is your commit; use --ours for a merge
python3 scripts/rename_example_names.py
git add -A && git rebase --continue
```

If you only added new tests or docs with the old name, run the script and commit the result.
