"""scripts/codemod_pkgmove.py (ADR 0015 phases 3 to 5) on a tiny synthetic package: it moves a package into a group
folder, rewrites dotted names and paths, keeps the old import path alive as the same module, and is idempotent."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = "backend/src/superaide"

FILES = {
    f"{SRC}/__init__.py": '"""Pkg."""\n\n\ndef main() -> None:\n    return None\n',
    f"{SRC}/core.py": (
        "from superaide.leafa import thing\nfrom superaide.leafb.sub import other\n\nVALUE = thing() + other()\n"
    ),
    f"{SRC}/leafa/__init__.py": "def thing() -> int:\n    return 1\n",
    f"{SRC}/leafb/__init__.py": "",
    f"{SRC}/leafb/sub.py": (
        "from pathlib import Path\n\nHERE = Path(__file__).resolve().parents[2]\n\n\n"
        "def other() -> int:\n    return 2\n"
    ),
    f"{SRC}/stay/__init__.py": "",
    "ci/modules.toml": f'[modules.a]\nsrc = ["{SRC}/leafa/**"]\n',
    "docs/note.md": "See backend/src/superaide/leafb/sub.py and `superaide.leafa`; superaide.leafabc stays.\n",
    "backend/tests/test_x.py": 'import superaide.leafb.sub\nPATCH = "superaide.leafa.thing"\n',
    ".github/CODEOWNERS": "/backend/src/superaide/leafa/   @owner\n",
}


def load():
    spec = importlib.util.spec_from_file_location("codemod_pkgmove", ROOT / "scripts" / "codemod_pkgmove.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["codemod_pkgmove"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel, text in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


MOVES = {"leafa": "features/leafa", "leafb": "features/leafb"}


def snapshot(root: Path) -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.split("\0")  # fmt: skip
    return {
        f: (root / f).read_text() for f in files if f.endswith((".py", ".md", ".toml", ".json")) or "CODEOWNERS" in f
    }


def test_packages_move_and_everything_that_names_them_follows(repo: Path) -> None:
    cm = load()
    assert set(cm.run(repo, MOVES)) == {"leafa", "leafb"}
    assert (repo / SRC / "features/leafa/__init__.py").exists() and not (repo / SRC / "leafa").exists()
    assert (repo / SRC / "features/__init__.py").exists()
    core = (repo / SRC / "core.py").read_text()
    assert (
        "from superaide.features.leafa import thing" in core
        and "from superaide.features.leafb.sub import other" in core
    )
    assert "parents[3]" in (repo / SRC / "features/leafb/sub.py").read_text()  # one level deeper
    assert 'src = ["backend/src/superaide/features/leafa/**"]' in (repo / "ci/modules.toml").read_text()
    assert "/backend/src/superaide/features/leafa/ " in (repo / ".github/CODEOWNERS").read_text()
    note = (repo / "docs/note.md").read_text()
    assert "superaide/features/leafb/sub.py" in note and "`superaide.features.leafa`" in note
    assert "superaide.leafabc stays" in note  # a longer name is not a match
    test = (repo / "backend/tests/test_x.py").read_text()
    assert "import superaide.features.leafb.sub" in test and '"superaide.features.leafa.thing"' in test
    moves = json.loads((repo / "ci/layout-moves.json").read_text())
    assert moves["aliases"]["superaide.leafa"] == "superaide.features.leafa"
    assert moves["prefixes"][f"{SRC}/leafa/"] == f"{SRC}/features/leafa/"


def test_the_old_import_path_is_the_same_module(repo: Path) -> None:
    load().run(repo, MOVES)
    code = (
        "import superaide.leafb.sub as old, superaide.features.leafb.sub as new, superaide.leafa as a, "
        "superaide.features.leafa as b\nassert old is new and a is b\nprint('same')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo,
        env={"PYTHONPATH": str(repo / "backend/src")},
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "same", out.stderr


def test_a_second_run_changes_nothing(repo: Path) -> None:
    cm = load()
    cm.run(repo, MOVES)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    before = snapshot(repo)
    assert cm.run(repo, MOVES) == {}
    assert snapshot(repo) == before


def test_the_real_phase_four_plan_names_the_thirteen_leaves() -> None:
    plan = load().PHASES[4]
    assert len(plan) == 13 and all(new == f"features/{old}" for old, new in plan.items())
