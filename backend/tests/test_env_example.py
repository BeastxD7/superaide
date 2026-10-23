"""backend/.env.example documents every environment variable the app reads, once, with the code's defaults."""

import importlib.util
import json
import re
from pathlib import Path

from superaide.config import Settings

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_env_example.py"

# Deliberate differences between the example and the code default.
EXAMPLE_DIFFERS = {"ENABLE_SIMULATOR"}  # the example turns the simulator on for local development


def _script():
    spec = importlib.util.spec_from_file_location("check_env_example", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_variable_is_documented_once():
    assert _script().problems() == []


def test_the_check_fails_when_a_variable_is_missing(tmp_path, monkeypatch):
    mod = _script()
    kept = [
        line
        for line in mod.BACKEND_EXAMPLE.read_text().splitlines()
        if not re.match(r"^#? ?(ECHO_FILTER_ENABLED|MAX_SPEND_USD)=", line)
    ]
    fake = tmp_path / ".env.example"
    fake.write_text("\n".join(kept))
    monkeypatch.setattr(mod, "BACKEND_EXAMPLE", fake)
    found = mod.problems()
    assert any("ECHO_FILTER_ENABLED" in p and "not documented" in p for p in found)
    assert any("MAX_SPEND_USD" in p for p in found)


def test_the_check_fails_on_a_duplicate(tmp_path, monkeypatch):
    mod = _script()
    fake = tmp_path / ".env.example"
    fake.write_text(mod.BACKEND_EXAMPLE.read_text() + "\n# DB_POOL_SIZE=10\n")
    monkeypatch.setattr(mod, "BACKEND_EXAMPLE", fake)
    assert any("DB_POOL_SIZE appears 2 times" in p for p in mod.problems())


def test_example_values_equal_the_code_defaults():
    example: dict[str, str] = {}
    for line in (ROOT / "backend" / ".env.example").read_text().splitlines():
        m = re.match(r"^#? ?([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            example[m.group(1)] = m.group(2).strip()
    wrong = []
    for name in Settings.model_fields:
        key = name.upper()
        if key not in example or key in EXAMPLE_DIFFERS:
            continue
        field = Settings.model_fields[name]
        default, text = field.get_default(call_default_factory=True), example[key]
        if isinstance(default, bool):
            same = text.lower() == str(default).lower()
        elif isinstance(default, dict):
            same = (json.loads(text) if text else {}) == default
        elif isinstance(default, int | float):
            same = text != "" and float(text) == float(default)
        else:
            same = text == str(default)
        if not same:
            wrong.append(f"{key}: example {text!r}, code {default!r}")
    assert not wrong, "\n".join(wrong)
