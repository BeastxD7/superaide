#!/usr/bin/env python3
"""Audit the test selector against what the tests really execute (ADR 0015, "Selecting tests").

    selection_audit.py --data .coverage                 # a run with: pytest --cov --cov-context=test
    selection_audit.py --data .coverage --scope module  # the widest rule (every test of a dependent module)
    selection_audit.py --data .coverage --scope tests   # only the tests that reach the changed code

For every source file F the coverage data says which tests ran code of F (the ground truth: a test that executes F
can fail when F changes). `select_tests.py --changed F` says which tests the selector would run. A *miss* is a test
that executes F and is not selected. The selector is safe to narrow only while the misses are zero, or explained.

Needs the data file from a run with coverage contexts (`--cov-context=test`): each line is then tied to the test that
ran it, including its fixtures. Run with `uv run --python 3.14 --no-project --with coverage`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "backend/src/superaide/"


def load_selector(root: Path):
    spec = importlib.util.spec_from_file_location(
        "select_tests", root / "scripts" / "select_tests.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["select_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


def executed_by(data_file: str, root: Path) -> dict[str, set[str]]:
    """source file (repo-relative) -> test files (repo-relative) that ran some of its lines."""
    from coverage import CoverageData

    data = CoverageData(basename=data_file)
    data.read()
    out: dict[str, set[str]] = defaultdict(set)
    for path in data.measured_files():
        rel = Path(path).as_posix()
        if "/backend/src/superaide/" not in rel:
            continue
        rel = SRC + rel.split("/backend/src/superaide/", 1)[1]
        for contexts in data.contexts_by_lineno(path).values():
            for ctx in contexts:
                nodeid = ctx.split("|", 1)[0]
                if "::" not in nodeid:  # "" is import time: no test owns it
                    continue
                out[rel].add(
                    "backend/" + nodeid.split("::", 1)[0].removeprefix("backend/")
                )
    return out


def audit(data_file: str, scope: str, root: Path, top: int) -> dict:
    st = load_selector(root)
    idx = st.build_index(root)
    idx.mmap.dependent_scope = scope
    dyn = executed_by(data_file, root)
    total_pairs = misses = 0
    per_file: dict[str, list[str]] = {}
    sizes: list[tuple[str, int, int]] = []
    for f, tests in sorted(dyn.items()):
        sel = st.select([f], idx)
        if sel.mode == "full":
            chosen = set(idx.backend_tests)
        else:
            chosen = {"backend/" + t for t in sel.backend_files}
        total_pairs += len(tests)
        missed = sorted(tests - chosen)
        misses += len(missed)
        if missed:
            per_file[f] = missed
        sizes.append((f, len(chosen), len(tests)))
    return {
        "scope": scope,
        "files": len(dyn),
        "pairs": total_pairs,
        "misses": misses,
        "files_with_misses": len(per_file),
        "worst": sorted(per_file.items(), key=lambda kv: -len(kv[1]))[:top],
        "sizes": sizes,
        "per_file": per_file,
    }


def render(r: dict) -> str:
    sizes = r["sizes"]
    lines = [
        f"### Selection audit, scope `{r['scope']}`",
        "",
        f"- {r['files']} source files ran in at least one test; {r['pairs']} (file, test) pairs",
        f"- **misses: {r['misses']}** (a test ran the file's code and was not selected), in {r['files_with_misses']} files",
        (
            f"- mean selected per file change: {sum(s[1] for s in sizes) / max(1, len(sizes)):.0f} test files; "
            f"mean that really run it: {sum(s[2] for s in sizes) / max(1, len(sizes)):.0f}"
        ),
    ]
    for f, missed in r["worst"]:
        lines.append(
            f"  - `{f.removeprefix(SRC)}`: {len(missed)} missed, e.g. {', '.join(m.removeprefix('backend/tests/') for m in missed[:3])}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--data",
        required=True,
        help="coverage data file (pytest --cov --cov-context=test)",
    )
    ap.add_argument("--scope", choices=["module", "tests", "both"], default="both")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json", help="write the full result here")
    args = ap.parse_args(argv)
    scopes = ["module", "tests"] if args.scope == "both" else [args.scope]
    results = [audit(args.data, s, Path(args.root), args.top) for s in scopes]
    print("\n\n".join(render(r) for r in results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=list))
    return 1 if any(r["misses"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
