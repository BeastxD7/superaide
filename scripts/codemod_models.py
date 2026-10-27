#!/usr/bin/env python3
"""Phase 7 of docs/decisions/0015-project-layout.md: split `platform/db/models.py` per module.

    codemod_models.py --dry-run     # print the plan, change nothing
    codemod_models.py               # move the classes, rewrite every import, keep a lazy re-export

What it does:
  1. each table class moves, with its own decorators and the comment lines directly above it, from
     backend/src/superaide/platform/db/models.py to the `models.py` of the module that owns it (HOME below;
     a new file, or appended to the module's existing one). Table names, columns and constraints do not change, so no
     migration is expected: `alembic upgrade head && alembic check` proves it;
  2. every `from superaide.platform.db.models import A, B` in the repository (code, tests, scripts) becomes one import
     per home, `Base`, `Scoped` and `TenantScoped` staying where they are;
  3. `superaide.platform.db.models.<Class>` in strings (monkeypatch targets) follows;
  4. `platform/db/models.py` keeps `Base`, `Scoped`, `TenantScoped`, the instance-scope listener and a module
     `__getattr__` that resolves the old names lazily for one release, and imports every home so that the metadata sees
     every table (Alembic, `create_all`).
It is idempotent: a class that is no longer in models.py is not moved again.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "backend/src/superaide"
MODELS = f"{SRC}/platform/db/models.py"
STAYS = {"Base", "Scoped", "TenantScoped"}

# class -> home module (dotted, under superaide). One place decides who owns a table.
HOME: dict[str, str] = {}
for home, classes in {
    "identity.models": "Person ChannelIdentity IdentityLinkEvent LinkChallenge IdentityClaim ChannelInvite",
    "memory.models": (
        "Context Event MemoryItem MemoryOrigin Episode ConsolidationRun MemoryImport Document DocumentChunk Contact"
        " OwnerPreference Onboarding"
    ),
    "approvals.models": "Approval",
    "mandate.models": "Mandate",
    "tooltrace.models": "ToolTrace",
    "actionlog.models": "ActionLog",
    "mcp.models": "McpPin",
    "brain.models": "SpendEntry OwnerModelPrice LlmProvider ModelRole",
    "identity.auth.models": "UserAccount UserSession AuthEvent AccountAudit AgentHire IntegrationSecret",
    "jobs.models": "Job",
    "intentions.models": "Intention",
    "features.journal.models": "JournalSetting Prediction",
    "features.mirror.models": "MirrorSetting MirrorNote",
    "skills.models": "InstalledSkill SkillRelease SkillTombstone",
    "integrations.calendar.models": "CalendarWatch",
}.items():
    for c in classes.split():
        HOME[c] = home
# AuditLog and AgentState stay: every module audits, and the agent state is the runtime's own.
PLATFORM = {"AuditLog": "platform.db.models", "AgentState": "platform.db.models"}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def tracked(root: Path) -> list[str]:
    return [p for p in git(root, "ls-files", "-z").split("\0") if p]


def rewrite_imports(
    text: str, home_of: dict[str, str], module: str = "superaide.platform.db.models"
) -> str:
    """Rewrite `from <module> import A, B as C` so each name comes from its home. Names that stay are kept."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    lines = text.split("\n")
    edits: list[tuple[int, int, list[str]]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module == module
            and node.level == 0
        ):
            continue
        if not any(a.name in home_of for a in node.names):
            continue
        indent = " " * node.col_offset
        groups: dict[str, list[str]] = {}
        for a in node.names:
            where = f"superaide.{home_of[a.name]}" if a.name in home_of else module
            groups.setdefault(where, []).append(
                a.name + (f" as {a.asname}" if a.asname else "")
            )
        out = []
        for where, names in groups.items():
            one = f"{indent}from {where} import {', '.join(names)}"
            out += (
                [one]
                if len(one) <= 110
                else [
                    f"{indent}from {where} import (",
                    *[f"{indent}    {n}," for n in names],
                    f"{indent})",
                ]
            )
        if node.lineno == (
            node.end_lineno or node.lineno
        ):  # keep a trailing comment (`# noqa: E402`)
            tail = lines[node.lineno - 1][node.end_col_offset :]
            if tail.strip():
                out[-1] += tail
        edits.append((node.lineno - 1, node.end_lineno or node.lineno, out))
    for start, end, repl in sorted(edits, reverse=True):
        lines[start:end] = repl
    return "\n".join(lines)


def rewrite_strings(text: str, home_of: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        return f"superaide.{home_of[m.group(1)]}.{m.group(1)}"

    names = "|".join(sorted(home_of, key=len, reverse=True))
    return re.sub(rf"superaide\.platform\.db\.models\.({names})\b", sub, text)


def class_blocks(src: str) -> dict[str, tuple[int, int]]:
    """class name -> (first line, last line), 0-based, including decorators and the comment lines right above."""
    tree = ast.parse(src)
    lines = src.split("\n")
    out: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            start = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
            while (
                start > 0
                and lines[start - 1].lstrip().startswith("#")
                and not lines[start - 1].startswith("# ---")
            ):
                start -= 1
            out[node.name] = (start, (node.end_lineno or node.lineno))
    return out


def names_used(code: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Name)} | {
        n.value.id
        for n in ast.walk(ast.parse(code))
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
    }


def import_map(src: str) -> dict[str, str]:
    """name -> the import line that provides it, for the header of models.py."""
    out: dict[str, str] = {}
    for node in ast.parse(src).body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module != "__future__"
        ):
            for a in node.names:
                out[a.asname or a.name] = f"from {node.module} import {a.name}" + (
                    f" as {a.asname}" if a.asname else ""
                )
        elif isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = f"import {a.name}" + (
                    f" as {a.asname}" if a.asname else ""
                )
    return out


def plan(root: Path) -> dict[str, list[str]]:
    src = (root / MODELS).read_text()
    blocks = class_blocks(src)
    todo: dict[str, list[str]] = {}
    for cls, home in HOME.items():
        if cls in blocks:
            todo.setdefault(home, []).append(cls)
    return todo


def apply(root: Path, dry: bool) -> None:
    models_path = root / MODELS
    src = models_path.read_text()
    blocks = class_blocks(src)
    todo = plan(root)
    for home, classes in sorted(todo.items()):
        print(f"{home}: {' '.join(classes)}")
    if dry or not todo:
        return
    lines = src.split("\n")
    imports = import_map(src)
    helpers = {"_tenant", "_agent", "EMAIL_MATCH_SQL"}
    moved_classes = {c for cs in todo.values() for c in cs}
    for home, classes in todo.items():
        path = root / SRC / (home.replace(".", "/") + ".py")
        bodies = ["\n".join(lines[s:e]) for s, e in (blocks[c] for c in classes)]
        code = "\n\n\n".join(bodies)
        used = names_used(code)
        need = sorted(n for n in used if n in imports)
        platform_names = sorted(n for n in used if n in STAYS or n in helpers)
        other_homes: dict[str, list[str]] = {}
        for n in sorted(used & set(HOME)):
            if n not in classes and HOME[n] != home:
                other_homes.setdefault(HOME[n], []).append(n)
        header = ["from __future__ import annotations", ""]
        header += sorted({imports[n] for n in need})
        if platform_names:
            header.append(
                f"from superaide.platform.db.models import {', '.join(platform_names)}"
            )
        for h, ns in sorted(other_homes.items()):
            header.append(f"from superaide.{h} import {', '.join(ns)}")
        if path.exists():
            existing = path.read_text()
            text = existing.rstrip("\n") + "\n\n\n" + code + "\n"
            # the imports the moved classes need go after the existing header
            m = list(re.finditer(r"^(from|import) .*$", existing, re.MULTILINE))
            at = m[-1].end() if m else 0
            extra = [ln for ln in header[2:] if ln not in existing]
            text = text[:at] + ("\n" + "\n".join(extra) if extra else "") + text[at:]
        else:
            doc = f'"""Tables of `{home.rsplit(".", 1)[0].replace(".", "/")}/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""\n\n'
            text = doc + "\n".join(header) + "\n\n\n" + code + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(root, "add", str(path.relative_to(root)))
    # remove the moved blocks from models.py (bottom up)
    for s, e in sorted((blocks[c] for c in moved_classes), reverse=True):
        del lines[s:e]
    new = "\n".join(lines)
    new = re.sub(r"\n{4,}", "\n\n\n", new)
    models_path.write_text(new)
    print(f"moved {len(moved_classes)} classes")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--rewrite-only", action="store_true", help="only rewrite imports and strings"
    )
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not args.rewrite_only:
        apply(root, args.dry_run)
    if args.dry_run:
        return 0
    home_of = {**HOME}
    for rel in tracked(root) + [
        p.relative_to(root).as_posix() for p in (root / SRC).rglob("*.py")
    ]:
        if (
            not rel.endswith((".py", ".md", ".toml", ".yml", ".yaml", ".json"))
            or rel == MODELS
        ):
            continue
        if (
            rel.startswith(("backend/alembic/versions/", "node_modules/"))
            or rel == "scripts/codemod_models.py"
        ):
            continue
        if rel in (
            f"{SRC}/platform/db/__init__.py",
            "backend/tests/repo/test_codemod_models.py",
        ):
            continue
        p = root / rel
        try:
            text = p.read_text()
        except UnicodeDecodeError, OSError:
            continue
        new = text
        if rel.endswith(".py"):
            new = rewrite_imports(new, home_of)
        new = rewrite_strings(new, home_of)
        if new != text:
            p.write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
