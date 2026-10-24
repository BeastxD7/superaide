"""The API contract snapshot (backend/openapi.snapshot.json) matches the live schema.

A schema change is what makes CI run the whole browser suite instead of the smoke subset
(docs/decisions/0013). Regenerate with `make openapi` and commit the file.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "openapi_snapshot.py"


def test_openapi_snapshot_is_current() -> None:
    spec = importlib.util.spec_from_file_location("openapi_snapshot", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.SNAPSHOT.exists(), "run `make openapi` and commit backend/openapi.snapshot.json"
    changed = "API schema changed: run `make openapi` and commit the snapshot"
    assert mod.SNAPSHOT.read_text() == mod.live_schema(), changed
