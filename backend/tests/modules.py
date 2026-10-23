"""Tag every test with its module (`pytest -m mod_memory`), from ci/modules.toml.

The map lives in the repository root (ci/modules.toml) and is read by scripts/select_tests.py, which CI uses
to pick the tests a change needs (docs/design/test-modules.md). Here it only adds markers, so a developer can
run one module's tests; if the map cannot be loaded the suite runs untagged rather than failing.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _selector():
    spec = importlib.util.spec_from_file_location("select_tests", ROOT / "scripts" / "select_tests.py")
    if spec is None or spec.loader is None:
        raise ImportError("scripts/select_tests.py not found")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["select_tests"] = mod  # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


def marker_name(module: str) -> str:
    return "mod_" + re.sub(r"\W", "_", module)


def register(config: pytest.Config) -> None:
    try:
        mmap = _selector().load_map(ROOT)
    except Exception:  # noqa: BLE001 - tagging is a convenience, never a reason to fail the run
        return
    config._module_map = mmap  # type: ignore[attr-defined]
    for name in mmap.modules:
        config.addinivalue_line("markers", f"{marker_name(name)}: tests of the {name} module (ci/modules.toml)")


def tag(config: pytest.Config, items: list[pytest.Item]) -> None:
    mmap = getattr(config, "_module_map", None)
    if mmap is None:
        return
    for item in items:
        try:
            rel = Path(str(item.path)).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            continue
        for name in mmap.test_owners(rel):
            item.add_marker(getattr(pytest.mark, marker_name(name)))
