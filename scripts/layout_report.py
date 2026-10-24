#!/usr/bin/env python3
"""Measure the project layout (docs/decisions/0015-project-layout.md). Repeatable, read-only.

    layout_report.py                  # Markdown report: sizes, flat directories, cycles, fan-in / fan-out
    layout_report.py --format json    # the same numbers as JSON
    layout_report.py --mermaid        # package dependency graph as a Mermaid flowchart
    layout_report.py --graph-doc      # the full docs/design/module-graph.md text
    layout_report.py --check-graph    # exit 1 when docs/design/module-graph.md is stale

The import graph is the one scripts/select_tests.py uses (ast, lazy and TYPE_CHECKING imports included), so
the numbers here and the CI test selection cannot disagree. Needs Python 3.14 to parse the backend:
`uv run --python 3.14 --no-project python scripts/layout_report.py`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH_DOC = "docs/design/module-graph.md"


def _load_selector():
    spec = importlib.util.spec_from_file_location("select_tests", ROOT / "scripts" / "select_tests.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("select_tests", mod)
    spec.loader.exec_module(mod)
    return mod


def package_of(file_id: str) -> str:
    """app.routes -> app ; config -> config (a top-level file is its own pseudo-package)."""
    return file_id.split(".")[0]


def sccs(nodes: list[str], edges: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components with more than one node (Tarjan, iterative)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in edges and nxt not in index:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return sorted(out, key=len, reverse=True)


def package_edges(graph) -> dict[str, Counter]:
    """package -> Counter(other package -> number of file-level imports)."""
    edges: dict[str, Counter] = defaultdict(Counter)
    for src, targets in graph.imports.items():
        for t in targets:
            a, b = package_of(src), package_of(t)
            if a != b:
                edges[a][b] += 1
    return edges


def count_dir(rel: str, patterns: tuple[str, ...]) -> int:
    base = ROOT / rel
    if not base.exists():
        return 0
    return sum(1 for p in base.rglob("*") if p.is_file() and p.suffix in patterns and "node_modules" not in p.parts)


def flat_dirs(rel: str, suffixes: tuple[str, ...], top: int = 8) -> list[tuple[str, int]]:
    """Directories ranked by the number of source files directly inside them."""
    base = ROOT / rel
    counts: Counter = Counter()
    for p in base.rglob("*"):
        if p.is_file() and p.suffix in suffixes and "node_modules" not in p.parts and ".next" not in p.parts:
            counts[p.parent.relative_to(ROOT).as_posix()] += 1
    return counts.most_common(top)


def measure(root: Path = ROOT) -> dict:
    st = _load_selector()
    graph = st.build_graph(root)
    src = root / st.SRC_ROOT
    pkgs: dict[str, dict] = {}
    for fid, path in graph.files.items():
        pkg = package_of(fid)
        entry = pkgs.setdefault(pkg, {"files": 0, "lines": 0, "is_dir": (src / pkg).is_dir()})
        entry["files"] += 1
        entry["lines"] += (root / path).read_text().count("\n")
    pedges = package_edges(graph)
    for pkg, entry in pkgs.items():
        out = pedges.get(pkg, Counter())
        entry["fan_out"] = len(out)
        entry["fan_in"] = sum(1 for other in pedges.values() if pkg in other)
    file_cycles = sccs(sorted(graph.imports), graph.imports)
    pkg_nodes = sorted(pkgs)
    pkg_cycles = sccs(pkg_nodes, {p: set(c) for p, c in pedges.items()})
    dirs_only = {p: e for p, e in pkgs.items() if e["is_dir"]}
    tests_dir = root / st.TESTS_ROOT
    flat_tests = sum(1 for p in tests_dir.glob("*.py") if p.name.startswith("test_"))
    return {
        "packages_total": len(pkgs),
        "packages_dirs": len(dirs_only),
        "toplevel_files": len(pkgs) - len(dirs_only),
        "backend_src_files": len(graph.files),
        "backend_src_lines": sum(e["lines"] for e in pkgs.values()),
        "backend_test_files": sum(1 for p in tests_dir.rglob("test_*.py")),
        "backend_tests_flat": flat_tests,
        "e2e_specs": sum(1 for p in (root / "e2e/tests").rglob("*.spec.ts")),
        "frontend_files": count_dir("frontend/src", (".ts", ".tsx")),
        "frontend_features": sorted(p.name for p in (root / "frontend/src/features").iterdir() if p.is_dir())
        if (root / "frontend/src/features").exists()
        else [],
        "alembic_versions": count_dir("backend/alembic/versions", (".py",)),
        "models_files": sorted(p for p in graph.files.values() if p.endswith("/models.py")),
        "packages": dict(sorted(pkgs.items(), key=lambda kv: -kv[1]["files"])),
        "flat": {
            "backend/src": flat_dirs("backend/src/superaide", (".py",)),
            "backend/tests": flat_dirs("backend/tests", (".py",), 4),
            "e2e/tests": flat_dirs("e2e/tests", (".ts",), 3),
            "frontend/src": flat_dirs("frontend/src", (".ts", ".tsx")),
        },
        "file_cycles": file_cycles,
        "package_cycles": pkg_cycles,
        "package_edges": {p: dict(c) for p, c in sorted(pedges.items())},
    }


def render_md(m: dict) -> str:
    out = ["# Layout report", ""]
    out += [
        f"- backend/src/superaide: {m['packages_dirs']} packages (directories) + {m['toplevel_files']} top-level files, "
        f"{m['backend_src_files']} .py files, {m['backend_src_lines']} lines",
        f"- backend/tests: {m['backend_test_files']} test files, {m['backend_tests_flat']} of them directly in backend/tests/",
        f"- e2e/tests: {m['e2e_specs']} specs; frontend/src: {m['frontend_files']} .ts/.tsx files, "
        f"{len(m['frontend_features'])} feature folders",
        f"- Alembic revisions: {m['alembic_versions']}; models.py files: {len(m['models_files'])}",
        "",
        "## Biggest flat directories (source files directly inside)",
        "",
    ]
    for area, rows in m["flat"].items():
        out.append(f"- {area}: " + ", ".join(f"`{d}` {n}" for d, n in rows))
    out += [
        "",
        "## Packages (files, lines, fan-in, fan-out)",
        "",
        "| package | files | lines | fan-in | fan-out |",
        "|---|---:|---:|---:|---:|",
    ]
    for p, e in m["packages"].items():
        out.append(f"| {p} | {e['files']} | {e['lines']} | {e['fan_in']} | {e['fan_out']} |")
    out += ["", "## Import cycles", ""]
    if not m["file_cycles"]:
        out.append("File level: none.")
    for c in m["file_cycles"]:
        pk = sorted({package_of(f) for f in c})
        out.append(f"- File level: {len(c)} files across {len(pk)} packages ({', '.join(pk)})")
    if not m["package_cycles"]:
        out.append("Package level: none.")
    for c in m["package_cycles"]:
        out.append(f"- Package level: {len(c)} packages in one cycle: {', '.join(c)}")
    return "\n".join(out) + "\n"


def render_mermaid(m: dict, min_edges: int = 1) -> str:
    lines = ["flowchart LR"]
    for src, targets in m["package_edges"].items():
        for dst, n in sorted(targets.items()):
            if n >= min_edges:
                lines.append(f"  {src.replace('.', '_')} --> {dst.replace('.', '_')}")
    return "\n".join(lines)


def graph_doc(m: dict) -> str:
    cyc = m["package_cycles"]
    biggest = ", ".join(cyc[0]) if cyc else "none"
    return (
        "# Module graph\n\n"
        "Generated by `scripts/layout_report.py --graph-doc`; do not edit by hand (a test fails when it is stale).\n"
        "An arrow `a --> b` means a file in package `a` imports a file in package `b` (lazy and TYPE_CHECKING imports "
        "included). See [ADR 0015](../decisions/0015-project-layout.md).\n\n"
        f"- Packages: {m['packages_total']}. Largest package-level cycle: {len(cyc[0]) if cyc else 0} packages ({biggest}).\n"
        f"- Largest file-level cycle: {len(m['file_cycles'][0]) if m['file_cycles'] else 0} files.\n\n"
        "```mermaid\n" + render_mermaid(m) + "\n```\n\n"
        "## Fan-in and fan-out\n\n| package | fan-in (imported by) | fan-out (imports) |\n|---|---:|---:|\n"
        + "\n".join(
            f"| {p} | {e['fan_in']} | {e['fan_out']} |"
            for p, e in sorted(m["packages"].items(), key=lambda kv: (-kv[1]["fan_in"], kv[0]))
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--mermaid", action="store_true")
    ap.add_argument("--graph-doc", action="store_true")
    ap.add_argument("--check-graph", action="store_true")
    ap.add_argument("--write-graph", action="store_true", help=f"write {GRAPH_DOC}")
    args = ap.parse_args(argv)
    m = measure()
    if args.check_graph:
        path = ROOT / GRAPH_DOC
        if not path.exists() or path.read_text() != graph_doc(m):
            print(f"{GRAPH_DOC} is stale: run scripts/layout_report.py --write-graph", file=sys.stderr)
            return 1
        return 0
    if args.write_graph:
        (ROOT / GRAPH_DOC).write_text(graph_doc(m))
        return 0
    if args.graph_doc:
        print(graph_doc(m), end="")
    elif args.mermaid:
        print(render_mermaid(m))
    elif args.format == "json":
        print(json.dumps(m, indent=2, default=list))
    else:
        print(render_md(m), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
