#!/usr/bin/env python3
"""The test impact map, in shadow mode (ADR 0015 "Selection audit", issue #520).

The map says, for every backend source file, which test files really ran code from it (coverage with one context per
test). A pull request that changes the file then needs: the tests of the module it belongs to, the tests the map lists
for it, and the always-run guards, instead of every test of every module that imports it.

    impact_map.py build  --data .coverage --out impact.json     # from `pytest --cov --cov-context=test`
    impact_map.py merge  a.json b.json --out impact.json        # the shards of one run
    impact_map.py drift  --map old.json --fresh new.json         # what the new run learned that the old map did not know
    impact_map.py shadow --map impact.json --base HEAD^1        # what a PR would run under the map, next to what runs

SHADOW MODE: nothing here chooses what CI runs. `shadow` prints a Markdown comparison for the job summary and always
exits 0. The real selection is still scripts/select_tests.py.

FAIL SAFE: whenever the map cannot be trusted for a change it says so and proposes the real (wide) selection:
the map is missing, older than MAX_AGE_DAYS, or does not know a changed source file; a test helper or a non-Python
file under backend/ changed; the real selector already runs everything. A wrong skip is worse than a slow run.

Needs Python 3.14 (it loads select_tests.py). `build` and `drift` also need the `coverage` package.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = "backend/src/superaide/"
TESTS = "backend/tests/"
VERSION = 1
MAX_AGE_DAYS = 7


def load_selector(root: Path = ROOT) -> Any:
    spec = importlib.util.spec_from_file_location(
        "select_tests", root / "scripts" / "select_tests.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["select_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


# --- building the map ----------------------------------------------------------------------------------


def executed_by(data_file: str) -> dict[str, set[str]]:
    """source file (repo-relative) -> test files (repo-relative) that ran some of its lines. A line run during import
    (no test context) belongs to no test; fixtures count (pytest-cov tags setup, call and teardown)."""
    from coverage import CoverageData

    data = CoverageData(basename=data_file)
    data.read()
    out: dict[str, set[str]] = defaultdict(set)
    for path in data.measured_files():
        posix = Path(path).as_posix()
        if "/backend/src/superaide/" not in posix:
            continue
        rel = SRC + posix.split("/backend/src/superaide/", 1)[1]
        for contexts in data.contexts_by_lineno(path).values():
            for ctx in contexts:
                nodeid = ctx.split("|", 1)[0]
                if "::" in nodeid:
                    out[rel].add(
                        TESTS
                        + nodeid.split("::", 1)[0]
                        .removeprefix("backend/tests/")
                        .removeprefix("tests/")
                    )
    return out


def tests_seen(data_file: str) -> set[str]:
    from coverage import CoverageData

    data = CoverageData(basename=data_file)
    data.read()
    return {
        TESTS
        + c.split("|", 1)[0]
        .split("::", 1)[0]
        .removeprefix("backend/tests/")
        .removeprefix("tests/")
        for c in data.measured_contexts()
        if "::" in c
    }


def build(
    data_file: str, commit: str = "", now: datetime | None = None
) -> dict[str, Any]:
    files = {f: sorted(t) for f, t in sorted(executed_by(data_file).items())}
    return {
        "version": VERSION,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "commit": commit,
        "files": files,
        "tests": sorted(tests_seen(data_file)),
    }


def merge(maps: list[dict[str, Any]]) -> dict[str, Any]:
    """The shards of one run: union per file; the oldest date wins, so one stale part makes the whole map stale."""
    files: dict[str, set[str]] = defaultdict(set)
    tests: set[str] = set()
    for m in maps:
        for f, ts in m.get("files", {}).items():
            files[f].update(ts)
        tests.update(m.get("tests", []))
    stamps = [m["generated_at"] for m in maps if m.get("generated_at")]
    return {
        "version": VERSION,
        "generated_at": min(stamps) if stamps else "",
        "commit": next((m["commit"] for m in maps if m.get("commit")), ""),
        "files": {f: sorted(t) for f, t in sorted(files.items())},
        "tests": sorted(tests),
    }


# --- proposing a selection -----------------------------------------------------------------------------


def stale(impact: dict[str, Any] | None, now: datetime) -> str:
    if not impact or impact.get("version") != VERSION or not impact.get("files"):
        return "no usable impact map"
    try:
        made = datetime.fromisoformat(impact["generated_at"])
    except KeyError, ValueError:
        return "the impact map has no date"
    if now - made > timedelta(days=MAX_AGE_DAYS):
        return f"the impact map is older than {MAX_AGE_DAYS} days ({made:%Y-%m-%d})"
    return ""


def propose(
    changed: list[str],
    idx: Any,
    impact: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """What the backend would run under the map, and why it fell back when it did. Never raises for a bad map."""
    st = sys.modules.get("select_tests") or load_selector()
    now = now or datetime.now(UTC)
    real = st.select(changed, idx)
    base = {"real_backend": real.backend_files, "real_mode": real.mode}
    if real.mode != "selected":
        return {
            **base,
            "mode": real.mode,
            "reason": "the real selector runs " + real.mode,
            "backend_files": real.backend_files,
        }
    why = stale(impact, now)
    assert impact is not None or why
    if not why:
        known = set(impact["files"]) if impact else set()
        for p in changed:
            if st.matches(p, idx.mmap.ignore):
                continue
            if p.startswith(TESTS) and not st.is_backend_test(p):
                why = f"a test helper changed: {p}"
            elif p.startswith("backend/") and not st.is_backend_test(p):
                if not (p.startswith(SRC) and p.endswith(".py")):
                    why = f"a backend file the map does not describe changed: {p}"
                elif p not in known:
                    why = f"the map does not know {p.removeprefix(SRC)}"
            if why:
                break
    if why:
        return {
            **base,
            "mode": "fallback",
            "reason": why,
            "backend_files": real.backend_files,
        }

    mmap = idx.mmap
    chosen: set[str] = {p for p in changed if st.is_backend_test(p)}
    owners: set[str] = set()
    for p in changed:
        if p.startswith(SRC):
            owners.update(mmap.owners(p))
        elif st.is_backend_test(p):
            owners.update(idx.test_owners(p))
    for o in owners:
        chosen.update(idx.tests_of(o))
    existing = set(idx.backend_tests)
    for p in changed:
        chosen.update(t for t in impact["files"].get(p, []) if t in existing)  # type: ignore[index]
    for m in mmap.modules.values():
        if m.always:
            chosen.update(idx.tests_of(m.name))
    chosen.update(t for t in mmap.root_smoke if t in existing)
    files = sorted(t.removeprefix("backend/") for t in chosen)
    return {**base, "mode": "impact", "reason": "", "backend_files": files}


def render_shadow(result: dict[str, Any], total: int) -> str:
    real, mine = result["real_backend"], result["backend_files"]
    lines = ["### Impact map (shadow: nothing here changes what runs)", ""]
    if result["mode"] == "impact":
        extra = sorted(set(real) - set(mine))
        lines += [
            f"- Backend tests this PR runs now: **{len(real)}** of {total}",
            f"- Under the impact map it would run: **{len(mine)}**",
            f"- Left out by the map: {len(extra)}"
            + (f" (e.g. `{'`, `'.join(extra[:3])}`)" if extra else ""),
        ]
        added = sorted(set(mine) - set(real))
        if added:
            lines.append(
                f"- Added by the map (the real rule would skip them): **{len(added)}**: `{'`, `'.join(added[:5])}`"
            )
    elif result["mode"] == "fallback":
        lines.append(
            f"- Falls back to the real selection ({len(real)} of {total}): {result['reason']}"
        )
    else:
        lines.append(f"- Same as the real selection: {result['reason']}")
    return "\n".join(lines) + "\n"


# --- drift ---------------------------------------------------------------------------------------------


def drift(old: dict[str, Any], fresh: dict[str, set[str]]) -> dict[str, Any]:
    """Pairs (file, test) a fresh run recorded that the old map did not have: what a stale map would have missed."""
    new: dict[str, list[str]] = {}
    pairs = 0
    for f, tests in fresh.items():
        gone = sorted(tests - set(old.get("files", {}).get(f, [])))
        pairs += len(tests)
        if gone:
            new[f] = gone
    return {
        "pairs": pairs,
        "new_pairs": sum(len(v) for v in new.values()),
        "files_with_new": len(new),
        "worst": sorted(new.items(), key=lambda kv: -len(kv[1]))[:8],
    }


def render_drift(d: dict[str, Any]) -> str:
    lines = [
        "### Impact map drift",
        "",
        (
            f"- {d['new_pairs']} of {d['pairs']} (file, test) pairs in this run were not in the previous map, "
            f"in {d['files_with_new']} files"
        ),
    ]
    for f, ts in d["worst"]:
        lines.append(
            f"  - `{f.removeprefix(SRC)}`: {len(ts)} new, e.g. `{ts[0].removeprefix(TESTS)}`"
        )
    return "\n".join(lines) + "\n"


# --- command line --------------------------------------------------------------------------------------


def read_map(path: str | None) -> dict[str, Any] | None:
    if not path or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text())
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--data", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--commit", default="")
    m = sub.add_parser("merge")
    m.add_argument("parts", nargs="+")
    m.add_argument("--out", required=True)
    d = sub.add_parser("drift")
    d.add_argument("--map", required=True)
    d.add_argument("--fresh", required=True)
    d.add_argument("--summary-file")
    s = sub.add_parser("shadow")
    s.add_argument("--map")
    s.add_argument("--base", default="HEAD^1")
    s.add_argument("--head", default="HEAD")
    s.add_argument("--changed", nargs="*")
    s.add_argument("--summary-file")
    s.add_argument("--json", action="store_true")
    s.add_argument("--root", default=str(ROOT))
    args = ap.parse_args(argv)

    if args.cmd == "build":
        Path(args.out).write_text(
            json.dumps(build(args.data, args.commit), indent=1) + "\n"
        )
        return 0
    if args.cmd == "merge":
        parts = [p for p in (read_map(x) for x in args.parts) if p]
        Path(args.out).write_text(json.dumps(merge(parts), indent=1) + "\n")
        return 0
    if args.cmd == "drift":
        old = read_map(args.map)
        fresh = {
            f: set(ts)
            for f, ts in (read_map(args.fresh) or {}).get("files", {}).items()
        }
        report = (
            render_drift(drift(old, fresh))
            if old and old.get("files")
            else "### Impact map drift\n\n- no previous map to compare with (the first run after this was added)\n"
        )
        print(report)
        if args.summary_file:
            with open(args.summary_file, "a") as fh:
                fh.write(report)
        return 0

    # shadow: never fails the build, whatever goes wrong
    try:
        root = Path(args.root)
        st = load_selector(root)
        idx = st.build_index(root)
        changed = (
            args.changed
            if args.changed is not None
            else st.changed_files(args.base, args.head, root)
        )
        result = propose(changed, idx, read_map(args.map))
        out = render_shadow(result, len(idx.backend_tests))
        print(json.dumps(result, indent=1) if args.json else out)
        if args.summary_file:
            with open(args.summary_file, "a") as fh:
                fh.write(out)
    except Exception as exc:  # noqa: BLE001 - shadow mode must never break CI
        print(f"impact map shadow skipped: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
