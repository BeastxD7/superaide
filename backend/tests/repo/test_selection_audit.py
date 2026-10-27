"""scripts/selection_audit.py reads which tests really ran which source files (coverage contexts) and compares it
with what the selector would run."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[3]


def load():
    spec = importlib.util.spec_from_file_location("selection_audit", ROOT / "scripts" / "selection_audit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["selection_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_it_maps_each_source_file_to_the_test_files_that_ran_its_lines(tmp_path: Path) -> None:
    audit = load()
    data_file = str(tmp_path / "cov.data")
    data = CoverageData(basename=data_file)
    src = "/work/backend/src/superaide/memory/store.py"
    other = "/work/backend/src/superaide/policy/actions.py"
    data.set_context("backend/tests/memory/test_a.py::test_one|run")
    data.add_lines({src: [1, 2, 3]})
    data.set_context("backend/tests/policy/test_b.py::test_two|setup")  # a fixture counts: it is part of the test
    data.add_lines({src: [3, 4], other: [9]})
    data.set_context("")  # import time: no test owns it
    data.add_lines({other: [1]})
    data.write()

    got = audit.executed_by(data_file, ROOT)

    assert got["backend/src/superaide/memory/store.py"] == {
        "backend/tests/memory/test_a.py",
        "backend/tests/policy/test_b.py",
    }
    assert got["backend/src/superaide/policy/actions.py"] == {"backend/tests/policy/test_b.py"}
