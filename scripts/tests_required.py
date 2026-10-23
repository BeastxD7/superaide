#!/usr/bin/env python3
"""Fail a feat or fix pull request that changes source code but no test file (CONTRIBUTING.md, ADR 0013).

Standard library only. Inputs: PR_TITLE, PR_LABELS (comma separated) in the environment and the changed
file names, one per line, on stdin. Exit 0 = ok, 1 = missing tests. GitHub annotations go to stdout.
"""

from __future__ import annotations

import os
import re
import sys

CHECKED_TYPES = {"feat", "fix"}
EXEMPT_LABEL = "tests-not-needed"
SOURCE_PREFIXES = ("backend/src/", "frontend/src/", "e2e/src/")
TEST_PREFIXES = ("backend/tests/", "e2e/tests/")
TITLE = re.compile(r"^(?P<type>[A-Za-z]+)(\([^)]*\))?!?:\s")
FRONTEND_TEST = re.compile(r"^frontend/.*\.(test|spec)\.[A-Za-z0-9]+$")


def pr_type(title: str) -> str | None:
    m = TITLE.match(title.strip())
    return m.group("type").lower() if m else None


def is_test(path: str) -> bool:
    return path.startswith(TEST_PREFIXES) or bool(FRONTEND_TEST.match(path))


def is_source(path: str) -> bool:
    return path.startswith(SOURCE_PREFIXES) and not is_test(path)


def check(title: str, labels: list[str], files: list[str]) -> tuple[bool, list[str]]:
    """Return (ok, annotation lines)."""
    kind = pr_type(title)
    if kind is None:
        return True, [
            f"::warning::PR title has no Conventional Commit prefix (feat:, fix:, docs: ...): {title!r}. "
            "Tests-required check skipped."
        ]
    if kind not in CHECKED_TYPES:
        return True, [f"Type {kind!r} is not checked: tests are not required."]
    if EXEMPT_LABEL in labels:
        return True, [f"Label {EXEMPT_LABEL!r} set: tests not required. The PR body must give a one-line reason."]
    sources = [f for f in files if is_source(f)]
    if not sources:
        return True, ["No source file under backend/src, frontend/src or e2e/src changed: tests not required."]
    tests = [f for f in files if is_test(f)]
    if tests:
        return True, [f"{len(tests)} test file(s) changed: ok."]
    shown = ", ".join(sources[:5]) + (" ..." if len(sources) > 5 else "")
    return False, [
        f"::error::A {kind} PR must ship tests. Source changed ({shown}) but no test file did.",
        "Add backend/tests/** (pytest, fake model), a frontend *.test.* / *.spec.* file or e2e/tests/**; "
        "security-relevant code also needs a negative or redteam test, and a flagged feature needs flag off and on.",
        f"If no test applies, add the {EXEMPT_LABEL!r} label, give a one-line reason in the PR body, then re-run "
        "this job. See CONTRIBUTING.md, 'Every change ships with tests'.",
    ]


def main() -> int:
    labels = [x.strip() for x in os.environ.get("PR_LABELS", "").split(",") if x.strip()]
    files = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    ok, lines = check(os.environ.get("PR_TITLE", ""), labels, files)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
