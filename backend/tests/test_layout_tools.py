# ci-module: repo
"""scripts/layout_report.py and scripts/check_boundaries.py (ADR 0015): the measurements and the warn-only check."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


lr = _load("layout_report")
cb = _load("check_boundaries")


def test_sccs_finds_cycles_and_ignores_chains() -> None:
    edges = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}, "e": set()}
    assert lr.sccs(sorted(edges), edges) == [["a", "b", "c"]]
    assert lr.sccs(["x", "y"], {"x": {"y"}, "y": set()}) == []


def test_package_of() -> None:
    assert lr.package_of("app.routes") == "app"
    assert lr.package_of("config") == "config"


def test_measure_reports_the_real_repository() -> None:
    m = lr.measure()
    assert m["packages_dirs"] > 20
    assert m["backend_test_files"] >= m["backend_tests_flat"] > 0
    assert m["packages"]["db"]["fan_in"] > 10
    text = lr.render_md(m)
    assert "## Import cycles" in text and "| db |" in text
    json.dumps(m, default=list)


def test_module_graph_doc_renders_a_mermaid_graph() -> None:
    # docs/design/module-graph.md is regenerated on demand (scripts/layout_report.py --write-graph); it is not
    # compared here because every new import would otherwise fail unrelated pull requests.
    text = lr.graph_doc(lr.measure())
    assert "```mermaid\nflowchart LR" in text and "## Fan-in and fan-out" in text
    assert (ROOT / lr.GRAPH_DOC).exists()


def test_public_entry_points() -> None:
    assert cb.is_public("memory")
    assert cb.is_public("memory.schemas")
    assert cb.is_public("memory.api")
    assert not cb.is_public("memory.sleep")
    assert not cb.is_public("memory.sleep.deep")


def test_boundary_check_flags_deep_imports_and_never_fails_without_strict(capsys) -> None:
    class G:
        imports = {
            "app.routes": {"memory.sleep", "memory", "app.gmail", "db.models"},
            "db.models": {"memory.models"},
        }

    assert cb.violations(G) == [("app.routes", "memory.sleep")]
    assert cb.main([]) == 0
    assert "import boundaries:" in capsys.readouterr().out


def test_baseline_ratchet(tmp_path) -> None:
    base = tmp_path / "base.json"
    assert cb.main(["--write-baseline", str(base)]) == 0
    assert cb.main(["--baseline", str(base), "--strict"]) == 0  # nothing new against itself
    base.write_text("[]")
    assert cb.main(["--baseline", str(base), "--strict"]) == 1  # everything is new against an empty baseline
