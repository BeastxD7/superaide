#!/usr/bin/env python3
"""The flag registry check (issue #402): backend/flags.toml is the one list of every feature flag.

Fails (exit 1, a list on stdout) when:
  * a boolean Settings field or *_ENABLED setting has no registry entry, or an entry names no setting;
  * an entry has no owner, no valid stage (experiment | beta | stable) or no `added` date;
  * a flag that is not `stable` has no `remove_by`, or one more than 90 days after `added`, or one in the past;
  * the number of flags is above `budget` (the budget only goes down);
  * a default in the registry differs from the code's default;
  * the FEATURE FLAGS index in backend/.env.example or docs/flags.md differs from what the registry generates.

    python3.14 scripts/check_flags.py            # check
    python3.14 scripts/check_flags.py --write    # regenerate the index and docs/flags.md from the registry

Needs Python 3.11+ (tomllib).
"""

from __future__ import annotations

import ast
import sys
import tomllib
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_env_example as env  # noqa: E402

ROOT = env.ROOT
REGISTRY = ROOT / "backend/flags.toml"
EXAMPLE = env.BACKEND_EXAMPLE
FLAGS_DOC = ROOT / "docs/flags.md"
STAGES = {"experiment", "beta", "stable"}
GROUPS = {
    "owner-safe": "Safe to turn on by yourself (cost and risk are bounded; see the README for setup):",
    "owner-decision": "Needs an owner decision first (spends money, sends data elsewhere, widens what Ava may do, or is public):",
}
MAX_DAYS = 90
BEGIN, END = "# BEGIN GENERATED FLAGS (scripts/check_flags.py --write)", "# END GENERATED FLAGS"


def load() -> dict:
    return tomllib.loads(REGISTRY.read_text())


def code_flags() -> dict[str, bool | None]:
    """Setting (env) name -> its default (True/False, None when not a literal), for every boolean or *_ENABLED field."""
    tree = ast.parse(env.CONFIG.read_text())
    out: dict[str, bool | None] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    if name in env.NOT_FROM_ENV:
                        continue
                    ann = ast.unparse(stmt.annotation)
                    if ann == "bool" or name.upper().endswith("_ENABLED"):
                        v = stmt.value
                        out[name.upper()] = (
                            v.value if isinstance(v, ast.Constant) and isinstance(v.value, bool) else None
                        )
    return out


def render_index(reg: dict) -> str:
    lines = [BEGIN]
    for group, title in GROUPS.items():
        lines.append(f"# {title}")
        for f in sorted((f for f in reg["flag"] if f["group"] == group), key=lambda f: f["name"]):
            lines.append(f"#   {f['name']:<31} {str(f['default']).lower():<6} {f['summary']}")
    lines.append(END)
    return "\n".join(lines)


def render_doc(reg: dict) -> str:
    out = [
        "# Feature flags",
        "",
        "Generated from [`backend/flags.toml`](../backend/flags.toml) by `scripts/check_flags.py --write`; do not edit by hand.",
        f"{len(reg['flag'])} flags (budget {reg['budget']}, target {reg['target']} owner-facing). "
        "A flag that is not `stable` has a remove-by date: it is promoted or deleted by then.",
        "",
        "| Flag | Default | Stage | Owner | Remove by | What it does |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for f in sorted(reg["flag"], key=lambda f: f["name"]):
        s = f["summary"].replace("|", "\\|")
        out.append(
            f"| `{f['name']}` | {str(f['default']).lower()} | {f['stage']} | {f['owner']} | {f.get('remove_by', '')} | {s} |"
        )
    return "\n".join(out) + "\n"


def splice(text: str, block: str) -> str | None:
    if BEGIN not in text or END not in text:
        return None
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return head + block + tail


def problems(today: date | None = None) -> list[str]:
    today = today or date.today()
    out: list[str] = []
    reg = load()
    flags = reg.get("flag", [])
    code = code_flags()
    names = [f.get("name", "") for f in flags]
    for n in sorted({n for n in names if names.count(n) > 1}):
        out.append(f"flags.toml: {n} is listed more than once")
    for n in sorted(set(code) - set(names)):
        out.append(f"flags.toml: {n} is a flag in config.py but has no registry entry")
    for n in sorted(set(names) - set(code)):
        out.append(f"flags.toml: {n} is in the registry but is not a flag in config.py (delete the entry)")
    if len(flags) > reg.get("budget", 0):
        out.append(
            f"flags.toml: {len(flags)} flags is above the budget of {reg.get('budget')} (delete one, never raise it)"
        )
    for f in flags:
        n = f.get("name", "?")
        if not f.get("owner"):
            out.append(f"flags.toml: {n} has no owner")
        if f.get("stage") not in STAGES:
            out.append(f"flags.toml: {n} has stage {f.get('stage')!r}, must be one of {sorted(STAGES)}")
        if f.get("group") not in GROUPS or not f.get("summary"):
            out.append(f"flags.toml: {n} needs a summary and a group ({sorted(GROUPS)})")
        try:
            added = date.fromisoformat(str(f.get("added")))
        except ValueError:
            out.append(f"flags.toml: {n} has no valid `added` date")
            continue
        if f.get("stage") != "stable":
            try:
                due = date.fromisoformat(str(f.get("remove_by")))
            except ValueError:
                out.append(f"flags.toml: {n} is {f.get('stage')} and needs a `remove_by` date")
                continue
            if (due - added).days > MAX_DAYS:
                out.append(f"flags.toml: {n} remove_by {due} is more than {MAX_DAYS} days after added {added}")
            if due < today:
                out.append(
                    f"flags.toml: {n} is overdue: remove_by {due} has passed (promote it to stable or delete it)"
                )
        if n in code and code[n] is not None and code[n] != f.get("default"):
            out.append(f"flags.toml: {n} default {f.get('default')} differs from the code default {code[n]}")
    if not out:
        example = EXAMPLE.read_text()
        if splice(example, render_index(reg)) != example:
            out.append(
                "backend/.env.example: the FEATURE FLAGS index differs from flags.toml (run scripts/check_flags.py --write)"
            )
        if not FLAGS_DOC.exists() or FLAGS_DOC.read_text() != render_doc(reg):
            out.append("docs/flags.md differs from flags.toml (run scripts/check_flags.py --write)")
    return out


def write() -> None:
    reg = load()
    new = splice(EXAMPLE.read_text(), render_index(reg))
    if new is None:
        sys.exit(f"{EXAMPLE}: the generated-flags markers are missing")
    EXAMPLE.write_text(new)
    FLAGS_DOC.write_text(render_doc(reg))


def main() -> int:
    if "--write" in sys.argv:
        write()
    found = problems()
    for line in found:
        print(line)
    if found:
        print(f"\n{len(found)} problem(s).")
        return 1
    print(f"ok: {len(load()['flag'])} flags registered (budget {load()['budget']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
