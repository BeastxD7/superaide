"""The owner's real name must not come back as an example, fixture or demo name (docs/design/example-names.md)."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "rename_example_names.py"


def _load():
    spec = importlib.util.spec_from_file_location("rename_example_names", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_disallowed_occurrences_remain() -> None:
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(["python3", str(SCRIPT), "--check"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mapping_is_per_layer_and_case_aware() -> None:
    m = _load()
    assert m.rewrite("Shashank Rao <shashank@example.com> SHASHANK", "backend/tests/x.py") == (
        "Jordan Whitfield <jordan@example.com> JORDAN"
    )
    assert m.rewrite("Shashank Rao, shashank-rao, Shashank K", "e2e/tests/x.ts") == (
        "Ananya Menon, ananya-menon, Ananya M"
    )
    assert m.rewrite("U_DEMO_SHASHANK ava_for_shashank_bot", "backend/src/x.py") == "U_DEMO_JORDAN ava_for_jordan_bot"


def test_rewrite_is_idempotent() -> None:
    m = _load()
    once = m.rewrite("Shashank Rao and shashank@example.com", "docs/a.md")
    assert m.rewrite(once, "docs/a.md") == once


def test_allowlist_keeps_the_real_owner_notes() -> None:
    m = _load()
    whole, lines = m.load_allowlist()
    assert "CLAUDE.md" in whole
    assert m.process("CLAUDE.md", whole, lines, write=False) == []
