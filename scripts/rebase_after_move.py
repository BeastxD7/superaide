#!/usr/bin/env python3
"""Carry a branch across a layout move (docs/decisions/0015-project-layout.md, "Freeze protocol").

A move PR renames files and rewrites imports, so a branch that predates it conflicts everywhere. This replays
the branch's own changes onto the moved tree, mechanically:

    git fetch origin
    git checkout -B my-branch-moved origin/main            # main with the move already merged
    python scripts/rebase_after_move.py --map ci/layout-moves.json --base <merge-base before the move> --branch my-branch

For every file the branch changed since --base: its old path is mapped to the new path, the module paths inside
(`superaide.channels.slack` ...) are rewritten in the base and the branch version, and the two are merged into the
file as it is on the new main (git merge-file, a normal 3-way merge). Conflicts are left as markers, listed at the
end. Nothing is committed: review, run the tests, commit.

The map (JSON, written by the move PR's codemod next to the move):
    {"paths":   {"backend/src/superaide/channels/slack.py": "backend/src/superaide/channels/slack/client.py"},
     "prefixes": {"backend/tests/test_slack": "backend/tests/channels/slack/test_slack"},
     "modules": {"superaide.channels.slack": "superaide.channels.slack.client"}}
`paths` (exact) wins over `prefixes` (longest first); `modules` rewrites dotted names, longest first.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def remap_path(path: str, m: dict) -> str:
    if path in m.get("paths", {}):
        return m["paths"][path]
    for old in sorted(m.get("prefixes", {}), key=len, reverse=True):
        if path.startswith(old):
            return m["prefixes"][old] + path[len(old) :]
    return path


def remap_text(text: str, m: dict) -> str:
    for old in sorted(m.get("modules", {}), key=len, reverse=True):
        # whole dotted names only: `superaide.channels.slack` must not rewrite `superaide.channels.slack_x`
        text = re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w])", m["modules"][old], text)
    return text


def git(*args: str, cwd: Path, check: bool = True, input_: bytes | None = None) -> bytes:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, input=input_)
    if check and res.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {res.stderr.decode()}")
    return res.stdout


def show(rev: str, path: str, cwd: Path) -> str | None:
    res = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=cwd, capture_output=True)
    return res.stdout.decode() if res.returncode == 0 else None


def replay(m: dict, base: str, branch: str, cwd: Path) -> list[str]:
    conflicts: list[str] = []
    changes = git("diff", "--name-status", "--no-renames", base, branch, cwd=cwd).decode().splitlines()
    for line in changes:
        status, path = line.split("\t", 1)
        new = remap_path(path, m)
        target = cwd / new
        if status == "D":
            if target.exists():
                target.unlink()
            continue
        theirs = remap_text(show(branch, path, cwd) or "", m)
        if status == "A" or not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(theirs)
            continue
        base_text = remap_text(show(base, path, cwd) or "", m)
        with tempfile.TemporaryDirectory() as tmp:
            b, t = Path(tmp, "base"), Path(tmp, "theirs")
            b.write_text(base_text)
            t.write_text(theirs)
            res = subprocess.run(
                ["git", "merge-file", "-L", "main", "-L", "base", "-L", branch, str(target), str(b), str(t)],
                cwd=cwd,
                capture_output=True,
            )
        if res.returncode > 0:
            conflicts.append(new)
    return conflicts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", required=True, help="JSON move map")
    ap.add_argument("--base", required=True, help="merge-base of the branch with main before the move")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--repo", default=".")
    args = ap.parse_args(argv)
    conflicts = replay(json.loads(Path(args.map).read_text()), args.base, args.branch, Path(args.repo).resolve())
    if conflicts:
        print("conflicts to resolve by hand:\n  " + "\n  ".join(conflicts))
        return 1
    print("branch replayed onto the moved tree without conflicts; review, test, commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
