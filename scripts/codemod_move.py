#!/usr/bin/env python3
"""Phase 1 of docs/decisions/0015-project-layout.md: put every test next to the module that owns it.

    codemod_move.py --dry-run          # print the plan, change nothing
    codemod_move.py                    # apply it (git mv, then rewrite what points at the moved files)
    codemod_move.py --root <repo>      # another checkout (the tests use a tiny synthetic one)

What it does, driven by ci/modules.toml (the owner of a backend test is its `tests` entry or its `# ci-module:`
line, exactly as scripts/select_tests.py reads it):
  1. backend/tests/test_x.py          -> backend/tests/<module>/test_x.py
     backend/tests/api/test_x.py      -> backend/tests/<module>/api/test_x.py   (HTTP tests keep their own folder, so
                                          the api fixtures stay scoped to them: <module>/api/conftest.py re-exports
                                          tests/api_support.py, the old tests/api/conftest.py)
     helpers listed in a module's `tests` move with it. redteam/, live/, fixtures/, conftest.py and the shared
     support modules nobody owns stay where they are.
  2. e2e/tests/x.spec.ts              -> e2e/tests/<module>/x.spec.ts  (the first module that lists the spec; the
     `journeys/` specs span modules and stay). Helpers imported by a moved spec follow their importer.
  3. Rewrites: python imports (`tests.test_loop` -> `tests.pipeline.test_loop`), `Path(__file__)` depth, relative
     TypeScript imports, every path string in tracked text files (docs, CODEOWNERS, workflows, shard timings, the
     module map), then the module map's per-module `tests` list becomes one glob `backend/tests/<module>/**`.
  4. Writes ci/layout-moves.json (the map scripts/rebase_after_move.py reads), so open branches can follow.

It is idempotent: a file already under its module's folder is not moved again and a second run changes nothing.
It never touches src/ and never moves a file it cannot give exactly one owner (it stops and says which).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = ("redteam", "live", "fixtures")
KEEP_NAMES = {"conftest.py", "__init__.py"}
TEXT_SKIP = ("node_modules/", ".git/", "CHANGELOG.md", "package-lock.json", "uv.lock", "ci/layout-moves.json")
API_SUPPORT = "backend/tests/api_support.py"
SUPPORT_OWNER = "repo"  # owns the shared API fixtures in the module map (not the always-run guards)


def load_selector(root: Path):
    spec = importlib.util.spec_from_file_location("select_tests", root / "scripts" / "select_tests.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["select_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def tracked(root: Path) -> list[str]:
    return [p for p in git(root, "ls-files", "-z").split("\0") if p]


# --- the plan ---------------------------------------------------------------------------------------------


def plan_backend(root: Path, st, idx) -> dict[str, str]:
    moves: dict[str, str] = {}
    problems: list[str] = []
    for rel in sorted(p for p in tracked(root) if p.startswith("backend/tests/") and p.endswith(".py")):
        parts = rel.removeprefix("backend/tests/").split("/")
        name = parts[-1]
        if name in KEEP_NAMES and parts != ["api", "conftest.py"]:
            continue
        if parts[0] in SKIP_DIRS:
            continue
        if parts == ["api", "conftest.py"]:
            moves[rel] = API_SUPPORT
            continue
        owners = idx.test_owners(rel)
        if not owners:
            continue  # shared support (modules.py, sharding.py, ...): stays
        if len(owners) > 1:
            problems.append(f"{rel} has several owners: {owners}")
            continue
        module = owners[0]
        if parts[0] == module:
            continue  # already home
        sub = ["api"] if parts[0] == "api" else []
        moves[rel] = "/".join(["backend/tests", module, *sub, name])
    if problems:
        raise SystemExit("cannot place:\n  " + "\n  ".join(problems))
    return moves


def plan_e2e(root: Path, st, idx) -> dict[str, str]:
    moves: dict[str, str] = {}
    mmap = idx.mmap
    specs = sorted(p for p in tracked(root) if p.startswith("e2e/tests/") and p.endswith(".spec.ts"))
    for rel in specs:
        parts = rel.removeprefix("e2e/tests/").split("/")
        if len(parts) != 1:
            continue  # journeys/ and anything already in a folder
        owners = mmap.e2e_owners(rel)
        if not owners:
            continue
        moves[rel] = f"e2e/tests/{owners[0]}/{parts[0]}"
    # helpers (non-spec .ts files directly in e2e/tests) follow the first spec that imports them
    for rel in sorted(p for p in tracked(root) if p.startswith("e2e/tests/") and p.endswith(".ts") and "/" not in p[10:]):
        if rel.endswith(".spec.ts"):
            continue
        stem = Path(rel).name.removesuffix(".ts")
        for spec, dest in moves.items():
            text = (root / spec).read_text()
            if re.search(rf"""from\s+['"]\./{re.escape(stem)}['"]""", text):
                moves[rel] = f"{Path(dest).parent.as_posix()}/{Path(rel).name}"
                break
    return moves


# --- rewriting ----------------------------------------------------------------------------------------------


def dotted(rel: str) -> str:
    return rel.removeprefix("backend/").removesuffix(".py").replace("/", ".")


def rewrite_python_imports(text: str, modmap: dict[str, str]) -> str:
    """`tests.test_loop` -> `tests.pipeline.test_loop` (longest known prefix of the dotted name wins)."""

    def sub(m: re.Match) -> str:
        parts = m.group(0).split(".")
        for n in range(len(parts), 1, -1):
            head = ".".join(parts[:n])
            if head in modmap:
                return ".".join([modmap[head], *parts[n:]])
        return m.group(0)

    text = re.sub(r"(?<![\w.])tests(?:\.\w+)+", sub, text)

    def from_package(m: re.Match) -> str:  # `from tests import test_loop` -> `from tests.pipeline import test_loop`
        names = [n.split()[0] for n in m.group(2).split(",")]
        homes = {n: modmap.get(f"tests.{n}") for n in names}
        if not all(homes.values()):
            return m.group(0)
        packages = {h.rsplit(".", 1)[0] for h in homes.values() if h}
        return f"{m.group(1)}from {packages.pop()} import {m.group(2)}" if len(packages) == 1 else m.group(0)

    return re.sub(r"^(\s*)from tests import ([\w, ]+)$", from_package, text, flags=re.M)


def deepen_file_paths(text: str, by: int) -> str:
    """`Path(__file__)...parents[2]` / `.parent.parent` reach the same directory from `by` levels deeper."""
    out = []
    for line in text.splitlines(keepends=True):
        if "Path(__file__)" in line:
            line = re.sub(r"(Path\(__file__\)[^\n]*?)parents\[(\d+)\]", lambda m: f"{m.group(1)}parents[{int(m.group(2)) + by}]", line)
            line = re.sub(r"(Path\(__file__\)(?:\.resolve\(\))?)((?:\.parent(?!\w))+)", lambda m: m.group(1) + m.group(2) + ".parent" * by, line)
            line = re.sub(
                r"Path\(__file__\)\.with_name\((\"[^\"]+\")\)", lambda m: f"Path(__file__).parents[{by}] / {m.group(1)}", line
            )
        out.append(line)
    return "".join(out)


def rewrite_ts_relative(text: str, old: str, new: str, moves: dict[str, str]) -> str:
    """Relative imports of a moved file, and of files that moved away from it, keep pointing at the same target."""
    old_dir, new_dir = Path(old).parent, Path(new).parent

    def sub(m: re.Match) -> str:
        quote, target = m.group(1), m.group(2)
        if not target.startswith("."):
            return m.group(0)
        resolved = (old_dir / target).as_posix()
        norm = str(Path(*[p for p in Path(resolved).parts])).replace("\\", "/")
        norm = Path(norm).as_posix()
        cands = [norm, norm + ".ts", norm + ".tsx"]
        dest = next((moves[c] for c in cands if c in moves), None)
        if dest is not None:
            dest = re.sub(r"\.tsx?$", "", dest) if not Path(norm).suffix else dest
        else:
            dest = norm
        rel = Path(__import__("os").path.relpath(dest, new_dir)).as_posix()
        if not rel.startswith("."):
            rel = "./" + rel
        return f"{m.group(0).split(quote)[0]}{quote}{rel}{quote}"

    return re.sub(r"(?:from\s+|import\()(['\"])(\.[^'\"]*)\1", sub, text)


def path_replacer(moves: dict[str, str]):
    """One regex over every old path: `backend/tests/x.py`, `tests/x.py` (relative to backend/) and e2e ones."""
    forms: dict[str, str] = {}
    for old, new in moves.items():
        forms[old] = new
        for prefix in ("backend/", "e2e/"):
            if old.startswith(prefix + "tests/"):
                forms[old.removeprefix(prefix)] = new.removeprefix(prefix)
    for old, new in list(forms.items()):  # regex-escaped spellings (.gitleaks.toml allowlists)
        forms[old.replace(".", r"\.")] = new.replace(".", r"\.")
    keys = sorted(forms, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w./\-])(?:" + "|".join(re.escape(k) for k in keys) + r")(?![\w\-])")
    return lambda text: pattern.sub(lambda m: forms[m.group(0)], text)


def rewrite_module_map(text: str, moved_by_module: dict[str, list[str]], extra: dict[str, list[str]] | None = None) -> str:
    """In ci/modules.toml: each module's explicit backend test entries become one glob (a directory rule)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    section = None
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\[modules\.([\w-]+)\]", line)
        if m:
            section = m.group(1)
        if section in moved_by_module and re.match(r"tests\s*=\s*\[\s*$", line):
            j = i + 1
            while not lines[j].lstrip().startswith("]"):
                j += 1
            moved = moved_by_module[section]
            kept = [b for b in lines[i + 1 : j] if not any(f'"{p}"' in b for p in moved)]
            added = [f'  "{e}",\n' for e in (extra or {}).get(section, [])]
            out += [line, f'  "backend/tests/{section}/**",\n', *added, *kept, lines[j]]
            i = j + 1
            continue
        if section in moved_by_module and re.match(r"tests\s*=\s*\[\]", line):
            out += ["tests = [\n", f'  "backend/tests/{section}/**",\n', "]\n"]
            i += 1
            continue
        out.append(line)
        i += 1
    return "".join(out)


# --- run -------------------------------------------------------------------------------------------------------


def run(root: Path, dry: bool = False) -> dict[str, str]:
    st = load_selector(root)
    idx = st.build_index(root)
    moves = {**plan_backend(root, st, idx), **plan_e2e(root, st, idx)}
    if dry or not moves:
        for old, new in moves.items():
            print(f"{old} -> {new}")
        print(f"{len(moves)} files to move")
        return moves

    # generated: module folders as packages, and each module's api/ conftest
    new_py_dirs: set[Path] = set()
    api_dirs: set[Path] = set()
    for old, new in moves.items():
        if new.startswith("backend/tests/") and new != API_SUPPORT:
            d = Path(new).parent
            if d.name == "api":
                api_dirs.add(d)
            while d != Path("backend/tests"):
                new_py_dirs.add(d)
                d = d.parent

    for old, new in moves.items():
        (root / new).parent.mkdir(parents=True, exist_ok=True)
        git(root, "mv", old, new)
    for d in sorted(new_py_dirs):
        init = root / d / "__init__.py"
        if not init.exists():
            init.write_text("")
            git(root, "add", str(init.relative_to(root)))
    for d in sorted(api_dirs):
        conf = root / d / "conftest.py"
        if not conf.exists():
            conf.write_text(
                '"""The HTTP API fixtures (tests/api_support.py) for this module\'s API tests."""\n\n'
                "from tests.api_support import *  # noqa: F403\n"
            )
            git(root, "add", str(conf.relative_to(root)))

    modmap = {dotted(o): dotted(n) for o, n in moves.items() if o.endswith(".py")}
    modmap["tests.api.conftest"] = "tests.api_support"
    replace_paths = path_replacer(moves)
    ts_moves = {o: n for o, n in moves.items() if o.endswith(".ts")}
    new_to_old = {n: o for o, n in moves.items()}

    for rel in tracked(root):
        if any(rel.startswith(s) or rel.endswith(s) for s in TEXT_SKIP):
            continue
        path = root / rel
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        orig = text
        if rel.endswith(".py") and rel.startswith("backend/"):
            text = rewrite_python_imports(text, modmap)
            if rel in new_to_old:
                text = deepen_file_paths(text, 1)
                text = re.sub(r"^# ci-module:.*\n", "", text, count=1, flags=re.M)
        if rel.endswith(".ts") and rel.startswith("e2e/"):
            old = new_to_old.get(rel, rel)
            text = rewrite_ts_relative(text, old, rel, ts_moves)
        if rel == "ci/modules.toml":
            by_module: dict[str, list[str]] = {}
            for o, n in moves.items():
                if o.startswith("backend/tests/") and n != API_SUPPORT:
                    mod = n.split("/")[2]
                    by_module.setdefault(mod, []).append(n)
            text = replace_paths(text)
            text = rewrite_module_map(text, by_module, {SUPPORT_OWNER: [API_SUPPORT]})
        else:
            text = replace_paths(text)
        if text != orig:
            path.write_text(text)

    timings = root / "e2e" / "shard-timings.json"
    if timings.exists():  # the keys were rewritten above; keep the file sorted as `shard.mjs --update` writes it
        data = json.loads(timings.read_text())
        timings.write_text(json.dumps(dict(sorted(data.items())), indent=2) + "\n")
    out = {"paths": moves, "prefixes": {}, "modules": modmap}
    (root / "ci" / "layout-moves.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"moved {len(moves)} files; wrote ci/layout-moves.json")
    return moves


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    run(Path(args.root), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
