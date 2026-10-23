#!/usr/bin/env python3
"""Replay the selector over merged pull requests (the evidence table in docs/design/test-modules.md).

    gh pr list --state merged --limit 15 --json number,title,files > prs.json
    uv run python scripts/dry_run.py prs.json

Uses the map and import graph of the CURRENT checkout, so a path that no longer exists counts as unmapped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import select_tests as st  # noqa: E402


def main() -> None:
    prs = json.loads(Path(sys.argv[1]).read_text())
    idx = st.build_index()
    total = len(idx.backend_tests)
    specs = len([f for f in idx.files if st.is_spec(f) and ".proxy." not in f and ".dev." not in f])
    print("| PR | Title | Backend files (transitive) | Backend files (direct) | e2e specs | Why |")
    print("| --- | --- | --- | --- | --- | --- |")
    sums = [0, 0, 0]
    for pr in prs:
        paths = [f["path"] for f in pr["files"]]
        rows = []
        for expansion in ("transitive", "direct"):
            idx.mmap.expansion = expansion
            sel = st.select(paths, idx)
            rows.append(sel)
        t, d = rows

        def n(sel: st.Selection) -> int:
            return total if sel.mode == "full" else len(sel.backend_files)

        e2e = "all" if t.mode == "full" else str(len(t.e2e_specs)) if t.e2e_specs else "smoke/none"
        why = t.reasons[0] if t.mode == "full" else (", ".join(t.changed_modules) or "docs only")
        sums[0] += n(t)
        sums[1] += n(d)
        sums[2] += total
        title = pr["title"][:60].replace("|", "/")
        print(f"| #{pr['number']} | {title} | {n(t)}/{total} | {n(d)}/{total} | {e2e}/{specs} | {why[:90]} |")
    print(
        f"| total | | {sums[0]}/{sums[2]} ({100 * sums[0] // sums[2]}%) "
        f"| {sums[1]}/{sums[2]} ({100 * sums[1] // sums[2]}%) | | |"
    )


if __name__ == "__main__":
    main()
