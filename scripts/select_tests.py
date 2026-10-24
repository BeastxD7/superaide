#!/usr/bin/env python3
"""Pick the tests a change needs (docs/design/test-modules.md).

    select_tests.py --base origin/main              # files from `git diff origin/main...HEAD`
    select_tests.py --changed a.py b.ts             # explicit files
    select_tests.py --module memory                 # the tests of one module and of its dependents
    select_tests.py --all                           # the full suite (what main and the nightly run)

Reads ci/modules.toml (which module owns which source, test and e2e spec) and builds the import graph of
backend/src with `ast`, so "who depends on this file" is computed from the code and cannot rot.

FAIL SAFE: a wrong skip is worse than a slow run, so every doubt selects the FULL suite: a shared file
(`[full]` in the map, a core module, a model, a migration, conftest, a lockfile, the pipeline itself), a
file no module owns, a parse error, a crash. Needs Python 3.14 syntax support to parse the backend, so CI
runs it with `uv run --python 3.14`; with anything older it fails safe to the full suite.

Output: one JSON object (`--format json`), a Markdown table for the job summary (`--format summary`), or
`key=value` lines for $GITHUB_OUTPUT (`--format github`).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_FILE = "ci/modules.toml"
SRC_ROOT = "backend/src/superaide"
TESTS_ROOT = "backend/tests"


# --- globs ---------------------------------------------------------------------------------------------

_glob_cache: dict[str, re.Pattern[str]] = {}


def glob_re(pattern: str) -> re.Pattern[str]:
    """`**` crosses directories, `*` and `?` stay inside one; `**/x` also matches a top-level `x`."""
    cached = _glob_cache.get(pattern)
    if cached:
        return cached
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    rx = re.compile("".join(out) + r"\Z")
    _glob_cache[pattern] = rx
    return rx


def matches(path: str, patterns: list[str]) -> bool:
    return any(glob_re(p).match(path) for p in patterns)


# --- the map -------------------------------------------------------------------------------------------


@dataclass
class Module:
    name: str
    src: list[str]
    tests: list[str]
    e2e: list[str]
    core: bool = False
    always: bool = False


@dataclass
class ModuleMap:
    modules: dict[str, Module]
    expansion: str
    ignore: list[str]
    full: list[str]
    # Composition roots (`main`, `cli`, `plugins`) import everything to wire it together. They are not dependents in
    # the sense that matters here: a change in a module cannot break what a root does except by breaking its own
    # tests, and counting them as dependents drags every module that main imports into every selection.
    roots: frozenset[str] = frozenset()
    # Backend tests run whenever any backend code changed, because they boot the composed app: a router that
    # no longer imports, a registration that is missing. They replace "everything that imports main".
    root_smoke: list[str] = field(default_factory=list)

    def owners(self, path: str) -> list[str]:
        return [m.name for m in self.modules.values() if matches(path, m.src)]

    def test_owners(self, path: str) -> list[str]:
        return [m.name for m in self.modules.values() if matches(path, m.tests)]

    def e2e_owners(self, path: str) -> list[str]:
        return [m.name for m in self.modules.values() if matches(path, m.e2e)]


def load_map(root: Path = ROOT) -> ModuleMap:
    raw = tomllib.loads((root / MAP_FILE).read_text())
    mods = {
        name: Module(
            name=name,
            src=list(d.get("src", [])),
            tests=list(d.get("tests", [])),
            e2e=list(d.get("e2e", [])),
            core=bool(d.get("core", False)),
            always=bool(d.get("always", False)),
        )
        for name, d in raw.get("modules", {}).items()
    }
    expansion = raw.get("settings", {}).get("expansion", "transitive")
    if expansion not in ("transitive", "direct"):
        raise ValueError(f"settings.expansion must be transitive or direct, not {expansion!r}")
    settings = raw.get("settings", {})
    return ModuleMap(
        mods,
        expansion,
        list(raw.get("ignore", {}).get("paths", [])),
        list(raw.get("full", {}).get("paths", [])),
        frozenset(settings.get("composition_roots", [])),
        list(settings.get("composition_smoke", [])),
    )


# --- the import graph ----------------------------------------------------------------------------------


def file_id(path: str) -> str:
    """backend/src/superaide/app/routes.py -> app.routes ; app/__init__.py -> app ; config.py -> config."""
    rel = path.removeprefix(SRC_ROOT + "/").removesuffix(".py")
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__root__"


def _imports(tree: ast.AST, own_id: str, is_package: bool, known: set[str]) -> set[str]:
    """Every superaide file this module imports (anywhere in the file, lazy imports and TYPE_CHECKING included)."""

    def resolve(dotted: str) -> str | None:
        parts = dotted.split(".") if dotted else []
        while parts:
            cand = ".".join(parts)
            if cand in known:
                return cand
            parts = parts[:-1]
        return None

    found: set[str] = set()
    pkg = own_id.split(".") if is_package else own_id.split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                mod = ".".join(base + (node.module.split(".") if node.module else []))
            elif node.module and (node.module == "superaide" or node.module.startswith("superaide.")):
                mod = node.module.removeprefix("superaide").lstrip(".")
            else:
                continue
            for alias in node.names:
                target = resolve(f"{mod}.{alias.name}".strip(".")) if alias.name != "*" else None
                # `from superaide.app import routes` names a submodule; `from superaide.app.routes import x` a name
                if target is None or target == "__root__":
                    target = resolve(mod)
                if target:
                    found.add(target)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("superaide."):
                    target = resolve(alias.name.removeprefix("superaide."))
                    if target:
                        found.add(target)
    found.discard(own_id)
    return found


@dataclass
class Graph:
    files: dict[str, str]  # file id -> repo path
    imports: dict[str, set[str]]  # file id -> file ids it imports
    importers: dict[str, set[str]] = field(default_factory=dict)  # reverse

    def closure(self, start: set[str], transitive: bool, skip: frozenset[str] = frozenset()) -> set[str]:
        """Files that depend on `start` (not including `start` itself unless it is reached back through a cycle).
        Files in `skip` (the composition roots) are neither reported nor walked through."""
        seen: set[str] = set()
        frontier = set(start)
        while frontier:
            nxt: set[str] = set()
            for f in frontier:
                for imp in self.importers.get(f, ()):
                    if imp not in seen and imp not in skip:
                        seen.add(imp)
                        nxt.add(imp)
            if not transitive:
                break
            frontier = nxt
        return seen


def build_graph(root: Path = ROOT) -> Graph:
    paths = sorted(p.relative_to(root).as_posix() for p in (root / SRC_ROOT).rglob("*.py"))
    files = {file_id(p): p for p in paths}
    known = set(files)
    imports: dict[str, set[str]] = {}
    for fid, p in files.items():
        tree = ast.parse((root / p).read_text(), filename=p)
        found = _imports(tree, fid, p.endswith("__init__.py"), known)
        if fid in ("db.models", "platform.db.models"):
            # db/models.py imports every package's models.py only to register the tables on the metadata. That
            # is not a behavioural dependency (changing a models.py already runs everything, see [full]) and
            # it would tie the whole code base into one cycle.
            found = {t for t in found if not t.endswith(".models")}
        imports[fid] = found
    importers: dict[str, set[str]] = defaultdict(set)
    for f, targets in imports.items():
        for t in targets:
            importers[t].add(f)
    return Graph(files, imports, dict(importers))


def test_imports(root: Path, graph: Graph) -> dict[str, tuple[set[str], set[str]]]:
    """backend test file -> (superaide files it imports, tests.<helper> modules it imports)."""
    known = set(graph.files)
    out: dict[str, tuple[set[str], set[str]]] = {}
    for p in sorted((root / TESTS_ROOT).rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        tree = ast.parse(p.read_text(), filename=rel)
        targets = _imports(tree, "tests.x", False, known)
        helpers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith("tests."):
                    helpers.add(node.module.removeprefix("tests.").replace(".", "/") + ".py")
                elif node.module == "tests":
                    helpers.update(a.name + ".py" for a in node.names)
        out[rel] = (targets, helpers)
    return out


# --- repository listing --------------------------------------------------------------------------------


def repo_files(root: Path = ROOT) -> list[str]:
    try:
        res = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        listed = [x for x in res.stdout.splitlines() if x and (root / x).exists()]
        if listed:
            return listed
    except OSError, subprocess.CalledProcessError:
        pass
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git/" not in p.as_posix()
    )


def is_backend_test(path: str) -> bool:
    return path.startswith(TESTS_ROOT + "/") and path.endswith(".py") and Path(path).name.startswith("test_")


def is_spec(path: str) -> bool:
    return path.startswith("e2e/tests/") and path.endswith(".spec.ts")


def smoke_specs(root: Path, files: list[str]) -> list[str]:
    out = []
    for f in files:
        if is_spec(f) and not re.search(r"\.(proxy|dev)\.spec\.ts$", f):
            try:
                if "@smoke" in (root / f).read_text():
                    out.append(f)
            except OSError:
                pass
    return out


# --- selection -----------------------------------------------------------------------------------------


@dataclass
class Selection:
    mode: str  # "full" | "selected" | "none"
    reasons: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)  # modules whose tests run (empty when full)
    changed_modules: list[str] = field(default_factory=list)
    backend_files: list[str] = field(default_factory=list)  # paths relative to backend/, [] when full or none
    e2e_mode: str = "all"  # "all" | "specs"
    e2e_specs: list[str] = field(default_factory=list)  # relative to e2e/
    vitest_mode: str = "all"
    vitest_files: list[str] = field(default_factory=list)  # relative to frontend/
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def backend_run(self) -> bool:
        return self.mode == "full" or bool(self.backend_files)

    def to_json(self) -> dict:
        return {
            "mode": self.mode,
            "reasons": self.reasons,
            "modules": self.modules,
            "changed_modules": self.changed_modules,
            "backend": {
                "mode": "all" if self.mode == "full" else "selected",
                "run": self.backend_run,
                "files": self.backend_files,
            },
            "e2e": {"mode": self.e2e_mode if self.mode != "full" else "all", "specs": self.e2e_specs},
            "vitest": {"mode": self.vitest_mode if self.mode != "full" else "all", "files": self.vitest_files},
            "counts": self.counts,
        }


def full(reason: str) -> Selection:
    return Selection(mode="full", reasons=[reason])


@dataclass
class Index:
    """Everything the selector needs about the checkout, built once."""

    root: Path
    mmap: ModuleMap
    graph: Graph
    files: list[str]
    backend_tests: list[str]
    test_deps: dict[str, tuple[set[str], set[str]]]
    directives: dict[str, str] = field(default_factory=dict)  # test file -> module named by its "# ci-module:" line

    def test_owners(self, path: str) -> list[str]:
        """Modules that own a test file: the map's `tests` globs, or the file's own `# ci-module: name` line."""
        owners = self.mmap.test_owners(path)
        declared = self.directives.get(path)
        if declared and declared not in owners:
            owners = [*owners, declared]
        return owners

    def tests_of(self, name: str) -> list[str]:
        return [t for t in self.backend_tests if name in self.test_owners(t)]

    def module_files(self, name: str) -> list[str]:
        return [fid for fid, p in self.graph.files.items() if name in self.mmap.owners(p)]


def build_index(root: Path = ROOT, files: list[str] | None = None) -> Index:
    mmap = load_map(root)
    graph = build_graph(root)
    listing = files if files is not None else repo_files(root)
    tests = sorted(f for f in listing if is_backend_test(f))
    directives: dict[str, str] = {}
    for t in tests:
        try:
            head = "\n".join((root / t).read_text().splitlines()[:8])
        except OSError:
            continue
        found = re.search(r"^# ci-module:\s*([\w-]+)\s*$", head, re.M)
        if found:
            directives[t] = found.group(1)
    return Index(root, mmap, graph, listing, tests, test_imports(root, graph), directives)


def select(changed: list[str], idx: Index, extra_ids: set[str] | None = None) -> Selection:
    mmap = idx.mmap
    changed_ids: set[str] = set(extra_ids or ())
    changed_modules: set[str] = set()
    direct_tests: set[str] = set()  # backend test files changed themselves
    helper_changed: set[str] = set()  # tests/<helper>.py, selects the module's tests and the tests importing it
    direct_specs: set[str] = set()
    direct_vitest: set[str] = set()
    notes: list[str] = []

    for path in sorted(set(changed)):
        if matches(path, mmap.ignore):
            notes.append(f"ignored (docs): {path}")
            continue
        if matches(path, mmap.full):
            return full(f"shared file, runs everything: {path}")
        # a test, a spec or a helper of some module
        if path.startswith(TESTS_ROOT + "/"):
            owners = idx.test_owners(path)
            if not owners:
                return full(f"test file no module owns: {path}")
            changed_modules.update(owners)
            if is_backend_test(path):
                direct_tests.add(path)
            else:
                helper_changed.add(path)
            continue
        if is_spec(path):
            if not mmap.e2e_owners(path):
                return full(f"e2e spec no module lists: {path}")
            direct_specs.add(path)
            continue
        if path.endswith((".test.ts", ".test.tsx")) and path.startswith("frontend/"):
            owners = mmap.test_owners(path)
            if not owners:
                return full(f"unit test no module owns: {path}")
            changed_modules.update(owners)
            direct_vitest.add(path)
            continue
        owners = mmap.owners(path)
        if not owners:
            return full(f"no module owns: {path}")
        for o in owners:
            if mmap.modules[o].core:
                return full(f"core module {o}: {path}")
        changed_modules.update(owners)
        fid = next((f for f, p in idx.graph.files.items() if p == path), None)
        if fid:
            changed_ids.add(fid)
        else:
            # a data file or a deleted/renamed python file: treat every file of the module as changed
            changed_ids.update(idx.module_files(owners[0]))

    transitive = mmap.expansion == "transitive"
    dependents = idx.graph.closure(changed_ids, transitive, mmap.roots)
    affected_ids = changed_ids | dependents
    affected = set(changed_modules)
    for fid in affected_ids:
        affected.update(mmap.owners(idx.graph.files[fid]))
    # drills has no test file of its own (the redteam guards cover it), so "owns backend code" counts too
    run_backend = bool(direct_tests) or any(
        idx.tests_of(n) or any(p.startswith("backend/") for p in mmap.modules[n].src) for n in affected
    )
    backend_set: set[str] = set(direct_tests)
    if run_backend:
        if changed_ids:
            backend_set.update(t for t in mmap.root_smoke if t in idx.backend_tests)
        for name in affected:
            backend_set.update(idx.tests_of(name))
        for t, (targets, helpers) in idx.test_deps.items():
            if is_backend_test(t) and (
                targets & affected_ids
                or helpers & {Path(h).as_posix().removeprefix(TESTS_ROOT + "/") for h in helper_changed}
            ):
                backend_set.add(t)
        for m in mmap.modules.values():
            if m.always:
                backend_set.update(idx.tests_of(m.name))
    backend_files = sorted(p.removeprefix("backend/") for p in backend_set)

    specs: set[str] = set(direct_specs)
    vitest: set[str] = set(direct_vitest)
    for name in affected:
        for f in idx.files:
            if is_spec(f) and matches(f, mmap.modules[name].e2e):
                specs.add(f)
            if (
                f.startswith("frontend/")
                and f.endswith((".test.ts", ".test.tsx"))
                and matches(f, mmap.modules[name].tests)
            ):
                vitest.add(f)
    if specs:
        specs.update(smoke_specs(idx.root, idx.files))

    sel = Selection(
        mode="selected" if (backend_files or specs or vitest) else "none",
        reasons=notes or ([] if (backend_files or specs or vitest) else ["nothing to test"]),
        modules=sorted(affected),
        changed_modules=sorted(changed_modules | {o for f in changed_ids for o in mmap.owners(idx.graph.files[f])}),
        backend_files=backend_files,
        e2e_mode="specs",
        e2e_specs=sorted(p.removeprefix("e2e/") for p in specs),
        vitest_mode="files",
        vitest_files=sorted(p.removeprefix("frontend/") for p in vitest),
    )
    sel.counts = {"backend_selected": len(backend_files), "backend_total": len(idx.backend_tests)}
    return sel


def select_module(name: str, idx: Index) -> Selection:
    """`make test MODULE=memory`: the module's own tests and those of everything that depends on it."""
    if name not in idx.mmap.modules:
        raise SystemExit(f"unknown module {name!r}; known: {', '.join(sorted(idx.mmap.modules))}")
    m = idx.mmap.modules[name]
    if m.core:
        return full(f"{name} is a core module")
    # pretend every code file of the module changed (not the [full] ones such as models.py)
    ids = {fid for fid in idx.module_files(name) if not matches(idx.graph.files[fid], idx.mmap.full)}
    sel = select([], idx, extra_ids=ids)
    for t in idx.tests_of(name):
        sel.backend_files = sorted(set(sel.backend_files) | {t.removeprefix("backend/")})
    if sel.mode == "none" and sel.backend_files:
        sel.mode = "selected"
    if name not in sel.modules:
        sel.modules = sorted({*sel.modules, name})
    if not sel.changed_modules:
        sel.changed_modules = [name]
    return sel


def with_total(sel: Selection, idx: Index) -> Selection:
    sel.counts.setdefault("backend_total", len(idx.backend_tests))
    sel.counts["e2e_total"] = len(
        [f for f in idx.files if is_spec(f) and not re.search(r"\.(proxy|dev)\.spec\.ts$", f)]
    )
    if sel.mode == "full":
        sel.counts["backend_selected"] = len(idx.backend_tests)
    return sel


# --- git -----------------------------------------------------------------------------------------------


def changed_files(base: str, head: str = "HEAD", root: Path = ROOT) -> list[str]:
    """Both the old and the new path of a rename (`--no-renames` reports a rename as delete + add)."""
    res = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", f"{base}...{head}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [x for x in res.stdout.splitlines() if x]


# --- rendering -----------------------------------------------------------------------------------------


def render_summary(sel: Selection) -> str:
    j = sel.to_json()
    lines = [
        "### Test selection",
        "",
        f"**Mode: {sel.mode}**" + (f" ({'; '.join(sel.reasons[:3])})" if sel.reasons else ""),
    ]
    sel_n, tot = sel.counts.get("backend_selected", 0), sel.counts.get("backend_total", 0)
    if sel.mode == "full":
        lines.append(f"- Backend: all {tot} test files, e2e: all specs, unit tests: all")
    else:
        lines.append(f"- Backend: {sel_n} of {tot} test files")
        lines.append(
            f"- e2e: {len(j['e2e']['specs'])} specs"
            + (f" of {sel.counts['e2e_total']}" if sel.counts.get("e2e_total") else "")
        )
        lines.append(f"- Unit tests (vitest): {len(j['vitest']['files'])} files")
        if sel.changed_modules:
            lines.append(f"- Changed modules: {', '.join(sel.changed_modules)}")
        if sel.modules:
            dependents = [m for m in sel.modules if m not in sel.changed_modules]
            lines.append(f"- Run because they depend on them: {', '.join(dependents) or 'none'}")
    lines.append("")
    lines.append("To force everything, add the `full-suite` label and re-run the `Changed paths` job.")
    return "\n".join(lines) + "\n"


def github_output(sel: Selection) -> str:
    j = sel.to_json()
    return "\n".join(
        [
            f"mode={sel.mode}",
            f"backend_run={'true' if sel.backend_run else 'false'}",
            f"selection={json.dumps(j, separators=(',', ':'))}",
        ]
    )


# --- command line --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="git ref to diff against (three-dot), e.g. origin/main")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--changed", nargs="*", help="explicit changed paths instead of git")
    ap.add_argument("--stdin", action="store_true", help="read changed paths, one per line, from stdin")
    ap.add_argument("--module", help="select one module and its dependents (make test MODULE=...)")
    ap.add_argument("--all", action="store_true", help="the full suite")
    ap.add_argument("--force-full", metavar="WHY", help="run everything, for this reason (push to main, label, ...)")
    ap.add_argument("--format", choices=["json", "summary", "github", "pytest"], default="json")
    ap.add_argument("--summary-file", help="append the Markdown summary here (CI passes $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args(argv)
    root = Path(args.root)
    try:
        idx = build_index(root)
        if args.all:
            sel = full("--all")
        elif args.force_full:
            sel = full(args.force_full)
        elif args.module:
            sel = select_module(args.module, idx)
        else:
            if args.stdin:
                paths = [ln.strip() for ln in sys.stdin if ln.strip()]
            elif args.changed is not None:
                paths = args.changed
            elif args.base:
                paths = changed_files(args.base, args.head, root)
            else:
                ap.error("give --base, --changed, --stdin, --module or --all")
            sel = select(paths, idx)
        sel = with_total(sel, idx)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure must fail safe, never skip
        sel = full(f"selector error, running everything: {type(exc).__name__}: {exc}")
    if args.summary_file:
        with open(args.summary_file, "a") as fh:
            fh.write(render_summary(sel))
    if args.format == "json":
        print(json.dumps(sel.to_json(), indent=2))
    elif args.format == "summary":
        print(render_summary(sel))
    elif args.format == "github":
        print(github_output(sel))
    else:  # ALL, NONE or the test files relative to backend/ (the Makefile runs them)
        print("ALL" if sel.mode == "full" else " ".join(sel.backend_files) or "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
