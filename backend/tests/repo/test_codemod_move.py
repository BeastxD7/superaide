"""scripts/codemod_move.py (ADR 0015 phase 1) on a tiny synthetic repository: it moves tests next to their module,
rewrites everything that pointed at them, and a second run changes nothing."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

MAP = """
[settings]
expansion = "transitive"

[modules.alpha]
src = ["backend/src/superaide/alpha/**"]
tests = [
  "backend/tests/test_a.py",
  "backend/tests/api/test_c.py",
]
e2e = ["e2e/tests/a.spec.ts"]

[modules.beta]
src = ["backend/src/superaide/beta/**"]
tests = [
  "backend/tests/test_b.py",
  "backend/tests/redteam/**",
]
e2e = ["e2e/tests/a.spec.ts", "e2e/tests/b.spec.ts"]
"""

FILES = {
    "ci/modules.toml": MAP,
    "backend/src/superaide/__init__.py": "",
    "backend/src/superaide/alpha/__init__.py": "",
    "backend/src/superaide/beta/__init__.py": "",
    "backend/tests/__init__.py": "",
    "backend/tests/conftest.py": "",
    "backend/tests/test_a.py": (
        "from pathlib import Path\n"
        "from tests.test_b import VALUE\n"
        "from tests.api.conftest import signup\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
    ),
    "backend/tests/test_b.py": "# ci-module: beta\nVALUE = 1\n",
    "backend/tests/api/__init__.py": "",
    "backend/tests/api/conftest.py": "def signup():\n    return 1\n",
    "backend/tests/api/test_c.py": "from tests.api.conftest import signup\n",
    "backend/tests/redteam/__init__.py": "",
    "backend/tests/redteam/test_r.py": "from tests.test_b import VALUE\n",
    "backend/tests/modules.py": "",
    "e2e/tests/a.spec.ts": "import { x } from '../src/fixtures'\nimport { h } from './_helper'\n",
    "e2e/tests/b.spec.ts": "import { x } from '../src/fixtures'\nimport('../src/env')\n",
    "e2e/tests/_helper.ts": "export const h = 1\n",
    "e2e/tests/journeys/j.spec.ts": "import { x } from '../../src/fixtures'\n",
    "e2e/src/fixtures.ts": "export const x = 1\n",
    "e2e/shard-timings.json": json.dumps({"tests/b.spec.ts": 2, "tests/a.spec.ts": 1}),
    "docs/guide.md": "Run backend/tests/test_a.py and tests/api/test_c.py, then e2e/tests/b.spec.ts.\n",
    ".gitleaks.toml": "paths = ['''(^|/)tests/test_b\\.py$''']\n",
}


def load():
    spec = importlib.util.spec_from_file_location("codemod_move", ROOT / "scripts" / "codemod_move.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["codemod_move"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel, text in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "select_tests.py", tmp_path / "scripts" / "select_tests.py")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def snapshot(root: Path) -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.split("\0")  # fmt: skip
    return {f: (root / f).read_text() for f in files if f and f.endswith((".py", ".ts", ".md", ".toml", ".json"))}


def test_tests_land_in_their_module_folder_and_everything_follows(repo: Path) -> None:
    cm = load()
    moves = cm.run(repo)
    assert moves["backend/tests/test_a.py"] == "backend/tests/alpha/test_a.py"
    assert moves["backend/tests/test_b.py"] == "backend/tests/beta/test_b.py"  # owner from its `# ci-module:` line
    assert moves["backend/tests/api/test_c.py"] == "backend/tests/alpha/api/test_c.py"
    assert moves["backend/tests/api/conftest.py"] == "backend/tests/api_support.py"
    assert moves["e2e/tests/a.spec.ts"] == "e2e/tests/alpha/a.spec.ts"  # the first module listing the spec
    assert moves["e2e/tests/_helper.ts"] == "e2e/tests/alpha/_helper.ts"  # follows its importer
    assert "backend/tests/redteam/test_r.py" not in moves and "e2e/tests/journeys/j.spec.ts" not in moves

    a = (repo / "backend/tests/alpha/test_a.py").read_text()
    assert "from tests.beta.test_b import VALUE" in a and "from tests.api_support import signup" in a
    assert "parents[3]" in a  # one folder deeper reaches the same directory
    assert (repo / "backend/tests/alpha/api/conftest.py").read_text().endswith("import *  # noqa: F403\n")
    assert (repo / "backend/tests/alpha/__init__.py").exists() and (
        repo / "backend/tests/alpha/api/__init__.py"
    ).exists()
    assert "from tests.beta.test_b import VALUE" in (repo / "backend/tests/redteam/test_r.py").read_text()
    assert "# ci-module" not in (repo / "backend/tests/beta/test_b.py").read_text()

    spec = (repo / "e2e/tests/alpha/a.spec.ts").read_text()
    assert "'../../src/fixtures'" in spec and "'./_helper'" in spec
    assert "import('../../src/env')" in (repo / "e2e/tests/beta/b.spec.ts").read_text()
    assert "'../../src/fixtures'" in (repo / "e2e/tests/journeys/j.spec.ts").read_text()  # untouched

    toml = (repo / "ci/modules.toml").read_text()
    assert '"backend/tests/alpha/**"' in toml and '"backend/tests/beta/**"' in toml and "test_a.py" not in toml
    assert '"backend/tests/redteam/**"' in toml
    assert json.loads((repo / "e2e/shard-timings.json").read_text()) == {
        "tests/alpha/a.spec.ts": 1,
        "tests/beta/b.spec.ts": 2,
    }
    assert (
        "backend/tests/alpha/test_a.py and tests/alpha/api/test_c.py, then e2e/tests/beta/b.spec.ts"
        in (repo / "docs/guide.md").read_text()
    )
    assert "tests/beta/test_b\\.py" in (repo / ".gitleaks.toml").read_text()
    written = json.loads((repo / "ci/layout-moves.json").read_text())
    assert written["paths"]["backend/tests/test_a.py"] == "backend/tests/alpha/test_a.py"
    assert written["modules"]["tests.test_b"] == "tests.beta.test_b"


def test_a_second_run_changes_nothing(repo: Path) -> None:
    cm = load()
    cm.run(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    before = snapshot(repo)
    assert cm.run(repo) == {}
    assert snapshot(repo) == before


def test_the_moved_tests_keep_their_owner_in_the_module_map(repo: Path) -> None:
    cm = load()
    cm.run(repo)
    st = cm.load_selector(repo)
    idx = st.build_index(repo)
    assert idx.test_owners("backend/tests/alpha/test_a.py") == ["alpha"]
    assert idx.test_owners("backend/tests/alpha/api/test_c.py") == ["alpha"]
    assert idx.test_owners("backend/tests/beta/test_b.py") == ["beta"]


def test_a_test_with_two_owners_stops_the_move(repo: Path) -> None:
    (repo / "backend/tests/test_b.py").write_text("# ci-module: alpha\nVALUE = 1\n")
    (repo / "ci/modules.toml").write_text(MAP.replace('"backend/tests/redteam/**"', '"backend/tests/test_b.py"'))
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    with pytest.raises(SystemExit, match="several owners"):
        load().run(repo)
