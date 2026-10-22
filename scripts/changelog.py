#!/usr/bin/env python3
"""Rewrite CHANGELOG.md from the merged pull requests on GitHub (needs the `gh` CLI, logged in).

    python3 scripts/changelog.py [owner/repo]
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = sys.argv[1] if len(sys.argv) > 1 else "BeastxD7/superaide"
GROUPS = {
    "feat": "Added",
    "fix": "Fixed",
    "test": "Tests",
    "docs": "Documentation",
    "build": "Dependencies",
    "chore": "Maintenance",
    "style": "Maintenance",
    "ci": "Maintenance",
}
HEADER = """# Changelog

Every change to `main` arrives through a pull request, so this is the audit trail of what changed, when and why.
Each line links to its pull request, which has the review notes, the before and after screenshots and the test output.

How it is kept: one entry per merged pull request, newest first, grouped by day and type (Added, Fixed, Tests, ...).
After you merge, run `scripts/changelog.py` (it rewrites this file from GitHub's merged pull requests), and publish a
GitHub Release with generated notes (`gh release create vYYYY.MM.DD --generate-notes`, grouped by `.github/release.yml`).

## Unreleased
"""


def group(title: str) -> str:
    m = re.match(r"\W*(\w+)[(:!]", title.strip())
    return GROUPS.get(m.group(1).lower(), "Changed") if m else "Changed"


def main() -> None:
    raw = subprocess.run(
        ["gh", "pr", "list", "-R", REPO, "--state", "merged", "--limit", "1000", "--json", "number,title,mergedAt"],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    prs = sorted(json.loads(raw), key=lambda p: p["mergedAt"], reverse=True)
    days: dict[str, dict[str, list[dict]]] = collections.OrderedDict()
    for p in prs:
        days.setdefault(p["mergedAt"][:10], collections.OrderedDict()).setdefault(group(p["title"]), []).append(p)
    out = [HEADER]
    for day, groups in days.items():
        out += [f"### {day}", ""]
        for name, items in groups.items():
            out.append(f"**{name}**")
            out += [f"- {' '.join(p['title'].split())} ([#{p['number']}](https://github.com/{REPO}/pull/{p['number']}))" for p in items]
            out.append("")
    Path(__file__).resolve().parents[1].joinpath("CHANGELOG.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
