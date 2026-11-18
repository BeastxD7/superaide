#!/usr/bin/env python3
"""The dependency policy check (issue #418, docs/dependencies.md). Standard library only.

Fails (exit 1, a list on stdout) when:
  * an ecosystem in .github/dependabot.yml has no `groups:` or is not scheduled weekly;
  * a dependency listed in docs/dependencies.md is no longer in the backend or frontend manifests;
  * the canary workflow is missing, or one of its test jobs is not `continue-on-error: true`;
  * an exact pin (`==`) in backend/pyproject.toml has no comment saying why.

    python3 scripts/check_dependency_policy.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github/dependabot.yml"
CANARY = ROOT / ".github/workflows/canary.yml"
DOC = ROOT / "docs/dependencies.md"
PYPROJECT = ROOT / "backend/pyproject.toml"
MANIFESTS = {"frontend": ROOT / "frontend/package.json", "e2e": ROOT / "e2e/package.json"}
# The riskiest dependencies named in the policy: (name as written in the doc table, manifest key to find).
RISKY = [("psycopg", "pyproject", "psycopg"), ("anthropic", "pyproject", "anthropic"), ("openai", "pyproject", "openai"),
         ("next", "frontend", "next"), ("react", "frontend", "react"), ("@playwright/test", "e2e", "@playwright/test")]  # fmt: skip


def dependabot_problems() -> list[str]:
    out: list[str] = []
    text = DEPENDABOT.read_text()
    blocks = re.split(r"\n  - package-ecosystem: ", text)[1:]
    if not blocks:
        return [".github/dependabot.yml: no ecosystems found"]
    for block in blocks:
        name = block.splitlines()[0].strip()
        if "groups:" not in block:
            out.append(f".github/dependabot.yml: {name} has no `groups:` (updates must be grouped)")
        if "interval: weekly" not in block:
            out.append(f".github/dependabot.yml: {name} is not scheduled weekly")
    return out


def doc_problems() -> list[str]:
    out: list[str] = []
    if not DOC.exists():
        return ["docs/dependencies.md is missing"]
    doc = DOC.read_text()
    py = PYPROJECT.read_text()
    for label, where, key in RISKY:
        if f"`{label}`" not in doc:
            out.append(f"docs/dependencies.md: the riskiest-dependency table does not name `{label}`")
        manifest = py if where == "pyproject" else MANIFESTS[where].read_text()
        pattern = rf'"{re.escape(key)}(\[[^\]]*\])?(>=|==|~=|<|>)' if where == "pyproject" else rf'"{re.escape(key)}":'
        if not re.search(pattern, manifest):
            out.append(f"docs/dependencies.md names `{label}` but {where} no longer depends on it (update the table)")
    return out


def canary_problems() -> list[str]:
    if not CANARY.exists():
        return [".github/workflows/canary.yml is missing"]
    text = CANARY.read_text()
    out: list[str] = []
    for job in ("python-next", "node-next"):
        m = re.search(rf"^  {job}:\n(.*?)(?=^  \S|\Z)", text, flags=re.S | re.M)
        if not m:
            out.append(f".github/workflows/canary.yml: job {job} is missing")
        elif "continue-on-error: true" not in m.group(1):
            out.append(f".github/workflows/canary.yml: job {job} must be `continue-on-error: true` (it only warns)")
    return out


def pin_problems() -> list[str]:
    out: list[str] = []
    for line in PYPROJECT.read_text().splitlines():
        if re.match(r'\s*"[A-Za-z0-9_.\[\]-]+==', line) and "#" not in line:
            out.append(f"backend/pyproject.toml: exact pin without a reason comment: {line.strip()}")
    return out


def problems() -> list[str]:
    return dependabot_problems() + doc_problems() + canary_problems() + pin_problems()


def main() -> int:
    found = problems()
    for p in found:
        print(p)
    if found:
        print(f"\n{len(found)} problem(s).")
        return 1
    print("ok: dependency policy holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
