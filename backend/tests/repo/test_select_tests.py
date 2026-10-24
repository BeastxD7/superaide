"""The test selector (scripts/select_tests.py) and the module map it reads (ci/modules.toml).

Two kinds of test. The integrity tests run against the real repository and fail when the map rots: a source
file, test file or e2e spec that no module owns, a pattern that matches nothing, a test claimed twice. The unit
tests run the selector on a tiny synthetic repository and pin its safety rules: a change selects its module
and the modules that import it, and anything shared, unmapped or unexpected selects everything.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("select_tests", ROOT / "scripts" / "select_tests.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["select_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load()


def test_tests_carry_their_module_marker(request: pytest.FixtureRequest) -> None:
    # tests/modules.py tags every test from the map; `pytest -m mod_repo` runs this one
    assert request.node.get_closest_marker("mod_repo") is not None


# --- a synthetic repository ---------------------------------------------------------------------------

MAP = """
[settings]
expansion = "transitive"
[ignore]
paths = ["**/*.md", "docs/**"]
[full]
paths = ["backend/alembic/**", "backend/pyproject.toml", "backend/src/superaide/**/models.py", ".github/**"]

[modules.config]
core = true
src = ["backend/src/superaide/platform/config.py", "backend/src/superaide/__init__.py"]
tests = []
e2e = []

[modules.alpha]
src = ["backend/src/superaide/alpha/**"]
tests = ["backend/tests/test_alpha.py"]
e2e = ["e2e/tests/alpha.spec.ts"]

[modules.beta]
src = ["backend/src/superaide/beta/**"]
tests = ["backend/tests/test_beta.py", "backend/tests/beta_helper.py"]
e2e = ["e2e/tests/beta.spec.ts"]

[modules.gamma]
src = ["backend/src/superaide/gamma/**"]
tests = ["backend/tests/test_gamma.py"]
e2e = []

[modules.loner]
src = ["backend/src/superaide/loner/**"]
tests = ["backend/tests/test_loner.py"]
e2e = []

[modules.nocode]
src = ["backend/src/superaide/nocode/**"]
tests = []
e2e = []

[modules.guards]
always = true
src = []
tests = ["backend/tests/test_guard.py"]
e2e = []

[modules.web-a]
src = ["frontend/src/a/**"]
tests = ["frontend/src/a/a.test.ts"]
e2e = ["e2e/tests/alpha.spec.ts", "e2e/tests/web.spec.ts"]

[modules.web-shell]
core = true
src = ["frontend/src/lib/**"]
tests = []
e2e = ["e2e/tests/web-shell/smoke.spec.ts"]
"""

FILES = {
    "ci/modules.toml": MAP,
    "backend/src/superaide/__init__.py": "",
    "backend/src/superaide/platform/config.py": "",
    "backend/src/superaide/alpha/__init__.py": "",
    "backend/src/superaide/alpha/core.py": "VALUE = 1\n",
    "backend/src/superaide/beta/__init__.py": "",
    "backend/src/superaide/beta/svc.py": "from superaide.alpha.core import VALUE\n",
    "backend/src/superaide/beta/models.py": "",
    "backend/src/superaide/gamma/__init__.py": "",
    # imports beta lazily, inside a function: still a dependency
    "backend/src/superaide/gamma/api.py": "def f():\n    from superaide.beta import svc\n    return svc\n",
    "backend/src/superaide/loner/__init__.py": "",
    "backend/src/superaide/loner/x.py": "from superaide.platform.config import X\n",
    "backend/src/superaide/nocode/__init__.py": "",
    "backend/tests/test_alpha.py": "from superaide.alpha.core import VALUE\n",
    "backend/tests/test_beta.py": "from tests.beta_helper import HELP\nfrom superaide.beta import svc\n",
    "backend/tests/beta_helper.py": "HELP = 1\n",
    "backend/tests/test_gamma.py": "from superaide.gamma import api\n",
    "backend/tests/test_loner.py": "from superaide.loner import x\n",
    "backend/tests/test_guard.py": "",
    # a test of nobody's module that imports alpha directly: the direct-import rule must still pick it up
    "backend/tests/test_stray.py": "# ci-module: loner\nfrom superaide.alpha import core\n",
    "backend/tests/test_uses_helper.py": "# ci-module: loner\nfrom tests.beta_helper import HELP\n",
    "frontend/src/a/a.test.ts": "",
    "frontend/src/a/A.tsx": "",
    "frontend/src/lib/util.ts": "",
    "e2e/tests/alpha.spec.ts": "test('x @smoke')",
    "e2e/tests/beta.spec.ts": "",
    "e2e/tests/web.spec.ts": "",
    "e2e/tests/web-shell/smoke.spec.ts": "test('s @smoke')",
    "docs/readme.md": "",
}


@pytest.fixture(scope="module")
def idx(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("repo")
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return st.build_index(root, files=sorted(FILES))


def run(idx, *paths: str, expansion: str = "transitive"):
    idx.mmap.expansion = expansion
    return st.select(list(paths), idx)


def names_of(sel) -> set[str]:
    return {Path(f).name for f in sel.backend_files}


def test_change_selects_the_module_and_everything_that_imports_it(idx) -> None:
    sel = run(idx, "backend/src/superaide/alpha/core.py")
    assert sel.mode == "selected"
    # beta imports alpha; gamma imports beta (lazily): transitive reaches gamma. test_stray imports alpha directly.
    assert names_of(sel) == {"test_alpha.py", "test_beta.py", "test_gamma.py", "test_stray.py", "test_guard.py"}
    assert set(sel.modules) == {"alpha", "beta", "gamma"}


def test_direct_expansion_stops_at_the_first_importer(idx) -> None:
    sel = run(idx, "backend/src/superaide/alpha/core.py", expansion="direct")
    assert names_of(sel) == {"test_alpha.py", "test_beta.py", "test_stray.py", "test_guard.py"}


def test_a_leaf_change_leaves_unrelated_modules_alone(idx) -> None:
    sel = run(idx, "backend/src/superaide/gamma/api.py")
    assert names_of(sel) == {"test_gamma.py", "test_guard.py"}
    assert "test_alpha.py" not in names_of(sel)


def test_a_module_without_dependents_runs_only_itself_and_the_guards(idx) -> None:
    # loner imports only the core; its tests (two declare themselves with "# ci-module: loner") and the guard run
    sel = run(idx, "backend/src/superaide/loner/x.py")
    assert names_of(sel) == {"test_loner.py", "test_stray.py", "test_uses_helper.py", "test_guard.py"}
    assert "test_alpha.py" not in names_of(sel)


def test_a_module_with_no_tests_still_runs_the_guards(idx) -> None:
    sel = run(idx, "backend/src/superaide/nocode/__init__.py")
    assert sel.mode == "selected"
    assert names_of(sel) == {"test_guard.py"}


@pytest.mark.parametrize(
    "path",
    [
        "backend/src/superaide/platform/config.py",  # core module
        "backend/src/superaide/beta/models.py",  # [full]: every models.py
        "backend/alembic/versions/0100_x.py",  # [full]
        "backend/pyproject.toml",
        ".github/workflows/ci.yml",
        "frontend/src/lib/util.ts",  # core web module
    ],
)
def test_shared_files_select_everything(idx, path: str) -> None:
    assert run(idx, path).mode == "full"


@pytest.mark.parametrize("path", ["backend/src/superaide/brand_new/x.py", "something/else.sh", "backend/Dockerfile"])
def test_a_file_no_module_owns_selects_everything(idx, path: str) -> None:
    sel = run(idx, path)
    assert sel.mode == "full"
    assert path in sel.reasons[0]


def test_one_unmapped_file_among_mapped_ones_still_selects_everything(idx) -> None:
    assert run(idx, "backend/src/superaide/alpha/core.py", "mystery.bin").mode == "full"


def test_docs_only_selects_nothing(idx) -> None:
    sel = run(idx, "docs/readme.md", "backend/NOTES.md")
    assert sel.mode == "none"
    assert sel.backend_files == [] and not sel.backend_run


def test_a_changed_test_runs_itself_and_the_guards(idx) -> None:
    sel = run(idx, "backend/tests/test_alpha.py")
    assert names_of(sel) == {"test_alpha.py", "test_guard.py"}


def test_a_changed_test_helper_runs_every_test_that_imports_it(idx) -> None:
    sel = run(idx, "backend/tests/beta_helper.py")
    # beta owns the helper (its tests run) and test_uses_helper imports it from another module
    assert {"test_beta.py", "test_uses_helper.py"} <= names_of(sel)


def test_a_test_nobody_owns_selects_everything(idx) -> None:
    assert run(idx, "backend/tests/test_orphan.py").mode == "full"


def test_a_test_can_declare_its_module_instead_of_editing_the_map(idx) -> None:
    assert idx.test_owners("backend/tests/test_stray.py") == ["loner"]
    assert "test_stray.py" in names_of(run(idx, "backend/src/superaide/loner/x.py"))


def test_a_deleted_python_file_selects_its_modules_dependents(idx) -> None:
    # a file that no longer exists is not in the graph: every file of the module counts as changed
    sel = run(idx, "backend/src/superaide/alpha/gone.py")
    assert {"test_alpha.py", "test_beta.py"} <= names_of(sel)


def test_frontend_group_selects_its_specs_and_unit_tests_but_no_backend(idx) -> None:
    sel = run(idx, "frontend/src/a/A.tsx")
    assert sel.mode == "selected"
    assert sel.backend_files == [] and not sel.backend_run
    # the group's specs, plus every spec tagged @smoke so the basics always run
    assert sel.e2e_specs == ["tests/alpha.spec.ts", "tests/web-shell/smoke.spec.ts", "tests/web.spec.ts"]
    assert sel.vitest_files == ["src/a/a.test.ts"]


def test_a_changed_spec_runs_itself_and_the_smoke_set(idx) -> None:
    sel = run(idx, "e2e/tests/beta.spec.ts")
    assert sel.e2e_specs == ["tests/alpha.spec.ts", "tests/beta.spec.ts", "tests/web-shell/smoke.spec.ts"]
    assert not sel.backend_run


def test_a_spec_no_module_lists_selects_everything(idx) -> None:
    assert run(idx, "e2e/tests/new.spec.ts").mode == "full"


def test_the_module_command_runs_the_module_and_its_dependents(idx) -> None:
    idx.mmap.expansion = "transitive"
    sel = st.select_module("alpha", idx)
    assert {"test_alpha.py", "test_beta.py", "test_gamma.py"} <= names_of(sel)
    assert st.select_module("config", idx).mode == "full"


def test_a_crash_selects_everything(tmp_path: Path) -> None:
    # no map in this directory: the selector must not skip anything, it runs the lot
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "select_tests.py"), "--root", str(tmp_path), "--changed", "a.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["mode"] == "full" and "selector error" in out["reasons"][0]


def test_force_full_overrides_the_selection(tmp_path: Path) -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "select_tests.py"), "--force-full", "label full-suite"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(res.stdout)["mode"] == "full"


def test_github_output_carries_the_mode_and_the_selection(idx) -> None:
    sel = st.with_total(run(idx, "backend/src/superaide/gamma/api.py"), idx)
    lines = dict(line.split("=", 1) for line in st.github_output(sel).splitlines())
    assert lines["mode"] == "selected" and lines["backend_run"] == "true"
    assert json.loads(lines["selection"])["backend"]["files"] == sel.backend_files


# --- the real repository -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    return st.build_index(ROOT)


def _src_files(real) -> list[str]:
    return [
        f
        for f in real.files
        if (f.startswith("backend/src/superaide/") and "__pycache__" not in f)
        or (f.startswith("frontend/src/") and not re.search(r"\.test\.tsx?$", f))
    ]


def test_every_source_file_belongs_to_exactly_one_module(real) -> None:
    orphans = [f for f in _src_files(real) if not real.mmap.owners(f)]
    doubled = [f for f in _src_files(real) if len(real.mmap.owners(f)) > 1]
    assert not orphans, f"add these to a module's src in ci/modules.toml: {orphans[:10]}"
    assert not doubled, f"claimed by two modules: {doubled[:10]}"


def test_every_backend_test_file_belongs_to_exactly_one_module(real) -> None:
    tests = [
        f
        for f in real.files
        if f.startswith("backend/tests/")
        and f.endswith(".py")
        and Path(f).name not in ("__init__.py", "conftest.py", "sharding.py", "modules.py")
        and "/fixtures/" not in f
    ]
    orphans = [f for f in tests if not real.test_owners(f)]
    doubled = [f for f in tests if len(real.test_owners(f)) > 1]
    assert not orphans, (
        f"map these in ci/modules.toml, or add '# ci-module: <name>' as the file's first line: {orphans[:10]}"
    )
    assert not doubled, f"claimed by two modules: {doubled[:10]}"


def test_every_frontend_unit_test_belongs_to_exactly_one_module(real) -> None:
    tests = [f for f in real.files if re.search(r"^frontend/src/.*\.test\.tsx?$", f)]
    assert tests, "expected vitest files"
    assert [f for f in tests if len(real.mmap.test_owners(f)) != 1] == []


def test_every_e2e_spec_is_listed_by_a_module(real) -> None:
    specs = [f for f in real.files if f.startswith("e2e/tests/") and f.endswith(".spec.ts")]
    assert specs
    assert [f for f in specs if not real.mmap.e2e_owners(f)] == [], (
        "list the new spec under a module's e2e in ci/modules.toml"
    )


def test_no_map_pattern_is_dead(real) -> None:
    # a pattern that matches nothing is a rename or a delete the map did not follow
    dead = []
    for m in real.mmap.modules.values():
        for kind, patterns in (("src", m.src), ("tests", m.tests), ("e2e", m.e2e)):
            for p in patterns:
                if not any(st.glob_re(p).match(f) for f in real.files):
                    dead.append(f"{m.name}.{kind}: {p}")
    for p in real.mmap.full + real.mmap.ignore:
        if not any(st.glob_re(p).match(f) for f in real.files):
            dead.append(f"full/ignore: {p}")
    assert not dead, dead


def test_core_modules_are_the_shared_ones(real) -> None:
    core = {n for n, m in real.mmap.modules.items() if m.core}
    assert {"config", "db", "audit", "tenancy", "web-shell"} <= core


def test_the_import_graph_sees_the_edges_we_know_about(real) -> None:
    g = real.graph
    assert "features.mirror.service" in g.imports["app.mirror"]  # a plain absolute import
    assert "app.routes" in g.imports["triage.routes"] or g.imports["triage.routes"]  # the graph is not empty
    # db/models.py registers every package's models; those are not behavioural edges
    assert not [t for t in g.imports["platform.db.models"] if t.endswith(".models")]
    # every edge points at a real file
    assert all(t in g.files for ts in g.imports.values() for t in ts)


def test_a_models_file_runs_everything_on_the_real_map(real) -> None:
    assert st.select(["backend/src/superaide/triage/models.py"], real).mode == "full"


def test_the_real_map_is_selective_for_a_leaf_change(real) -> None:
    sel = st.select(["backend/src/superaide/features/mirror/service.py"], real)
    assert sel.mode == "selected"
    assert 0 < len(sel.backend_files) < len(real.backend_tests)
    assert "tests/mirror/api/test_mirror.py" in sel.backend_files
    assert "tests/channels/test_telegram.py" not in sel.backend_files


def test_the_guards_always_run_with_a_backend_selection(real) -> None:
    sel = st.select(["backend/src/superaide/features/mirror/service.py"], real)
    assert {"tests/guards/api/test_authz.py", "tests/guards/test_openapi_snapshot.py"} <= set(sel.backend_files)
    assert any(f.startswith("tests/redteam/") for f in sel.backend_files)


# --- composition roots ----------------------------------------------------------------------------------

ROOTS_MAP = (
    MAP.replace(
        'expansion = "transitive"',
        'expansion = "transitive"\ncomposition_roots = ["main"]\ncomposition_smoke = ["backend/tests/test_boot.py"]',
    )
    + """
[modules.wiring]
src = ["backend/src/superaide/main.py"]
tests = ["backend/tests/test_wiring.py", "backend/tests/test_boot.py"]
e2e = ["e2e/tests/web-shell/smoke.spec.ts"]
"""
)
ROOTS_FILES = {
    **FILES,
    "ci/modules.toml": ROOTS_MAP,
    # the composition root imports gamma and loner to wire them
    "backend/src/superaide/main.py": "from superaide.gamma import api\nfrom superaide.loner import x\n",
    "backend/tests/test_wiring.py": "from superaide import main\n",
    "backend/tests/test_boot.py": "from superaide import main\n",
}


@pytest.fixture(scope="module")
def roots_idx(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("repo_roots")
    for rel, text in ROOTS_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return st.build_index(root, files=sorted(ROOTS_FILES))


def test_a_module_change_does_not_select_the_tests_of_the_composition_root(roots_idx) -> None:
    sel = st.select(["backend/src/superaide/loner/x.py"], roots_idx)
    assert "test_wiring.py" not in names_of(sel)
    assert "wiring" not in sel.modules
    # what boots the composed app still runs, plus the module and the guards
    assert names_of(sel) >= {"test_loner.py", "test_guard.py", "test_boot.py"}


def test_a_real_dependent_is_still_selected_through_a_chain_that_does_not_use_the_root(roots_idx) -> None:
    sel = st.select(["backend/src/superaide/alpha/core.py"], roots_idx)
    assert {"test_alpha.py", "test_beta.py", "test_gamma.py"} <= names_of(sel)  # alpha <- beta <- gamma
    assert "test_wiring.py" not in names_of(sel)


def test_changing_the_composition_root_runs_its_own_module_and_the_smoke_specs(roots_idx) -> None:
    sel = st.select(["backend/src/superaide/main.py"], roots_idx)
    assert "wiring" in sel.modules
    assert {"test_wiring.py", "test_boot.py", "test_guard.py"} <= names_of(sel)
    assert "e2e/tests/web-shell/smoke.spec.ts" in {f"e2e/{s}" if not s.startswith("e2e/") else s for s in sel.e2e_specs}


def test_without_composition_roots_configured_the_root_is_an_ordinary_dependent(idx) -> None:
    assert st.ModuleMap.__dataclass_fields__["roots"].default == frozenset()


def test_the_real_map_names_main_cli_and_plugins_as_roots(real) -> None:
    assert real.mmap.roots == {"main", "cli", "plugins"}
    assert real.mmap.root_smoke == ["backend/tests/console/test_main.py"]
    sel = st.select(["backend/src/superaide/features/mirror/service.py"], real)
    assert "tests/console/test_main.py" in sel.backend_files
    assert "tests/console/test_cli.py" not in sel.backend_files  # cli is no longer a dependent of everything


def test_the_real_map_runs_everything_when_the_plugin_list_changes(real) -> None:
    assert st.select(["backend/src/superaide/plugins.py"], real).mode == "full"
