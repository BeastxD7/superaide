#!/usr/bin/env python3
"""The experiment sunset check (issue #405). Standard library only (Python 3.11+).

An EXPERIMENT_ flag in backend/flags.toml has a `sunset` date. When that date has passed and the entry has no
`result` (what the experiment taught, and whether it was kept or removed), this lists it and, with --open-issues,
opens one GitHub issue per experiment (through the `gh` CLI; an open issue with the same title is not duplicated).

    python3 scripts/check_experiments.py                 # list overdue experiments (exit 1 when there are any)
    python3 scripts/check_experiments.py --open-issues   # also open an issue for each (the weekly workflow)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "backend/flags.toml"
PREFIX = "EXPERIMENT_"


def overdue(reg: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for f in reg.get("flag", []):
        if not f.get("name", "").startswith(PREFIX) or str(f.get("result") or "").strip():
            continue
        try:
            sunset = date.fromisoformat(str(f.get("sunset")))
        except ValueError:
            continue  # check_flags.py reports a missing or bad date
        if sunset < today:
            out.append(f)
    return out


def issue_title(f: dict) -> str:
    return f"Experiment {f['name']} passed its sunset date ({f['sunset']}) with no result"


def issue_body(f: dict) -> str:
    return (
        f"`{f['name']}` was due to end on {f['sunset']} and has no `result` in `backend/flags.toml`.\n\n"
        f"- Hypothesis: {f.get('hypothesis', '')}\n- Metric: {f.get('metric', '')}\n\n"
        "Decide: promote it (rename without the prefix, stage `stable`, remove the experiment fields) or delete the flag "
        "and the code behind it. Either way record what the metric showed in `result`, or remove the entry. "
        "(Opened by scripts/check_experiments.py, issue #405.)"
    )


def open_issue(f: dict) -> bool:
    title = issue_title(f)
    found = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--search", f'"{title}" in:title', "--json", "title"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    if any(i["title"] == title for i in json.loads(found.stdout or "[]")):
        return False
    subprocess.run(
        ["gh", "issue", "create", "--title", title, "--body", issue_body(f), "--label", "type:chore"],
        check=True,
    )
    return True


def main() -> int:
    reg = tomllib.loads(REGISTRY.read_text())
    due = overdue(reg)
    for f in due:
        print(f"{f['name']}: sunset {f['sunset']} has passed and there is no result")
    if "--open-issues" in sys.argv:
        for f in due:
            print(("opened: " if open_issue(f) else "already open: ") + issue_title(f))
        return 0
    if due:
        return 1
    print("ok: no experiment is past its sunset date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
