#!/usr/bin/env python3
"""Fail when an environment variable the app reads is missing from its example file.

Sources of truth (read with the standard library only, so this runs anywhere):
  * backend/src/superaide/platform/config.py  every `Settings` field is an environment variable (NAME = FIELD.upper(),
    or its alias). Fields that are never read from the environment are listed in NOT_FROM_ENV below.
  * backend/src/superaide/**/*.py    literal os.environ.get("NAME") / os.getenv("NAME") reads.
  * frontend/src and next.config.ts  process.env.NAME reads (NODE_ENV is Next's own).

Documented in backend/.env.example means a line `NAME=...` or a commented-out `# NAME=...` (one space after
the `#`). Each name must appear exactly once. Every boolean Settings field must also be listed in the
FEATURE FLAGS index at the top of that file (the text before the END FEATURE FLAGS INDEX marker).
Every backend variable must also appear in docs/configuration.md (generated from the example file by
scripts/gen_configuration_doc.py), as `NAME` in a table row.
Frontend variables must appear in frontend/.env.example the same way.

Usage: python3 scripts/check_env_example.py   (exit 1 with a list when something is missing)
"""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "backend/src/superaide/platform/config.py"
BACKEND_SRC = ROOT / "backend/src/superaide"
BACKEND_EXAMPLE = ROOT / "backend/.env.example"
FRONTEND_EXAMPLE = ROOT / "frontend/.env.example"
CONFIG_DOC = ROOT / "docs/configuration.md"
INDEX_END = "END FEATURE FLAGS INDEX"

# Settings fields that are filled from the database or never serialised, not read from .env.
NOT_FROM_ENV = {"use_platform_ai", "llm_providers"}
# Variables the frameworks set themselves.
FRAMEWORK_ENV = {"NODE_ENV"}
# Set by the program for its own subprocess or by tests, not an operator setting.
INTERNAL_ENV: set[str] = set()

ENTRY = re.compile(r"^#? ?([A-Z][A-Z0-9_]*)=")


def settings_fields() -> dict[str, str]:
    """Env name -> annotation source, for every Settings field read from the environment."""
    tree = ast.parse(CONFIG.read_text())
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    if name in NOT_FROM_ENV:
                        continue
                    env = name.upper()
                    value = stmt.value
                    if isinstance(value, ast.Call):
                        for kw in value.keywords:
                            if kw.arg in {"alias", "validation_alias"} and isinstance(kw.value, ast.Constant):
                                env = str(kw.value.value).upper()
                    out[env] = ast.unparse(stmt.annotation)
    return out


def direct_backend_reads() -> set[str]:
    """Literal names passed to os.environ.get / os.getenv (regex, so any Python version can run this)."""
    pattern = re.compile(r"""(?:os\.environ\.get|os\.getenv|environ\.get)\(\s*["']([A-Za-z0-9_]+)["']""")
    names: set[str] = set()
    for path in BACKEND_SRC.rglob("*.py"):
        names.update(pattern.findall(path.read_text()))
    return names


def frontend_reads() -> set[str]:
    names: set[str] = set()
    files = [ROOT / "frontend/next.config.ts", *(ROOT / "frontend/src").rglob("*.ts*")]
    for path in files:
        if path.is_file():
            names.update(re.findall(r"process\.env\.([A-Z][A-Z0-9_]*)", path.read_text()))
    return names - FRAMEWORK_ENV


def documented(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    if path.exists():
        for line in path.read_text().splitlines():
            m = ENTRY.match(line)
            if m:
                counts[m.group(1)] += 1
    return counts


def problems() -> list[str]:
    out: list[str] = []
    fields = settings_fields()
    backend_needed = set(fields) | (direct_backend_reads() - INTERNAL_ENV)
    have = documented(BACKEND_EXAMPLE)
    for name in sorted(backend_needed - set(have)):
        out.append(f"backend/.env.example: {name} is read by the backend but not documented")
    for name, n in sorted(have.items()):
        if n > 1:
            out.append(f"backend/.env.example: {name} appears {n} times (must be exactly once)")

    text = BACKEND_EXAMPLE.read_text() if BACKEND_EXAMPLE.exists() else ""
    if INDEX_END not in text:
        out.append(f"backend/.env.example: the '{INDEX_END}' marker is missing")
    else:
        index = text.split(INDEX_END)[0]
        for name, ann in sorted(fields.items()):
            if ann == "bool" and not re.search(rf"\b{name}\b", index):
                out.append(f"backend/.env.example: boolean {name} is not in the FEATURE FLAGS index")

    doc = CONFIG_DOC.read_text() if CONFIG_DOC.exists() else ""
    in_doc = set(re.findall(r"^\| `([A-Z][A-Z0-9_]*)` \|", doc, flags=re.M))
    for name in sorted(backend_needed - in_doc):
        out.append(f"docs/configuration.md: {name} is read by the backend but not in the configuration reference")

    fe_have = documented(FRONTEND_EXAMPLE)
    for name in sorted(frontend_reads() - set(fe_have)):
        out.append(f"frontend/.env.example: {name} is read by the frontend but not documented")
    for name, n in sorted(fe_have.items()):
        if n > 1:
            out.append(f"frontend/.env.example: {name} appears {n} times (must be exactly once)")
    return out


def main() -> int:
    found = problems()
    for line in found:
        print(line)
    if found:
        print(f"\n{len(found)} problem(s). Add the variable to the example file with a comment and its default.")
        return 1
    n_backend = len(set(settings_fields()) | direct_backend_reads())
    print(f"ok: {n_backend} backend and {len(frontend_reads())} frontend variables are documented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
