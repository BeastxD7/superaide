#!/usr/bin/env python3
"""Import-boundary check (docs/decisions/0015-project-layout.md). WARN-ONLY until the owner turns it on.

Rule: a package talks to another package only through its public entry point: the package itself
(`superaide.memory`), `superaide.memory.api`, or `superaide.memory.schemas` / `models` (types). Importing
`superaide.memory.sleep` from outside `memory/` is a violation. Tests are not checked.

    check_boundaries.py                 # prints a summary and GitHub warnings, exits 0
    check_boundaries.py --strict        # exit 1 when there is a violation not in the baseline
    check_boundaries.py --write-baseline ci/boundary-baseline.json
    check_boundaries.py --baseline ci/boundary-baseline.json --strict   # ratchet: no NEW violations

Uses the same import graph as scripts/select_tests.py. Run with `uv run --python 3.14 --no-project`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout_report as lr  # noqa: E402

PUBLIC_NAMES = {"api", "schemas", "models", "contracts"}
# Registration only: db/models.py imports every package's models to fill the metadata.
EXEMPT_SOURCES = {"db.models"}


def is_public(target: str) -> bool:
    parts = target.split(".")
    return len(parts) == 1 or parts[1] in PUBLIC_NAMES and len(parts) == 2


def violations(graph) -> list[tuple[str, str]]:
    out = []
    for src, targets in sorted(graph.imports.items()):
        if src in EXEMPT_SOURCES:
            continue
        for t in sorted(targets):
            if lr.package_of(src) != lr.package_of(t) and not is_public(t):
                out.append((src, t))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--baseline")
    ap.add_argument("--write-baseline")
    ap.add_argument("--annotations", type=int, default=0, help="print N GitHub ::warning lines (default 0)")
    args = ap.parse_args(argv)
    st = lr._load_selector()
    graph = st.build_graph(lr.ROOT)
    found = violations(graph)
    keys = [f"{a} -> {b}" for a, b in found]
    if args.write_baseline:
        Path(args.write_baseline).write_text(json.dumps(sorted(keys), indent=1) + "\n")
    known = set(json.loads(Path(args.baseline).read_text())) if args.baseline else set()
    new = [k for k in keys if k not in known]
    by_pkg = Counter(lr.package_of(a) for a, _ in found)
    print(
        f"import boundaries: {len(found)} cross-package imports bypass a public entry point "
        f"({len(new)} not in the baseline); worst importers: " + ", ".join(f"{p} {n}" for p, n in by_pkg.most_common(5))
    )
    for k in new[: args.annotations]:
        print(f"::warning title=import boundary::{k} (import the package's public entry point instead)")
    if args.strict and new:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
