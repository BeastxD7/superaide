"""backend/flags.toml lists every feature flag with an owner, a stage and a remove-by date (issue #402)."""

import importlib.util
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_flags.py"


def _script():
    spec = importlib.util.spec_from_file_location("check_flags", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def flags():
    return _script()


def _with(flags, monkeypatch, edit):
    """Run the check against the real registry after `edit(registry)`; the generated-file checks are skipped
    by the edits themselves failing first."""
    reg = flags.load()
    edit(reg)
    monkeypatch.setattr(flags, "load", lambda: reg)
    return flags.problems(today=date(2026, 11, 16))


def test_the_registry_is_complete_and_the_generated_files_match(flags):
    assert flags.problems(today=date(2026, 11, 16)) == []


def test_a_setting_without_an_entry_fails(flags, monkeypatch):
    found = _with(
        flags, monkeypatch, lambda r: r["flag"].remove(next(f for f in r["flag"] if f["name"] == "ECHO_FILTER_ENABLED"))
    )
    assert any("ECHO_FILTER_ENABLED" in p and "no registry entry" in p for p in found)


def test_an_entry_for_a_setting_that_is_gone_fails(flags, monkeypatch):
    found = _with(flags, monkeypatch, lambda r: r["flag"].append({**r["flag"][0], "name": "GONE_ENABLED"}))
    assert any("GONE_ENABLED" in p and "not a flag" in p for p in found)


@pytest.mark.parametrize("stage", [None, "alpha", ""])
def test_a_missing_or_unknown_stage_fails(flags, monkeypatch, stage):
    def edit(r):
        f = next(f for f in r["flag"] if f["name"] == "ECHO_FILTER_ENABLED")
        f.pop("stage", None)
        if stage is not None:
            f["stage"] = stage

    assert any("ECHO_FILTER_ENABLED" in p and "stage" in p for p in _with(flags, monkeypatch, edit))


def test_a_missing_owner_fails(flags, monkeypatch):
    found = _with(flags, monkeypatch, lambda r: next(f for f in r["flag"] if f["name"] == "ACTION_LOG").pop("owner"))
    assert any("ACTION_LOG has no owner" in p for p in found)


def test_an_overdue_flag_fails(flags, monkeypatch):
    reg = flags.load()
    monkeypatch.setattr(flags, "load", lambda: reg)
    found = flags.problems(today=date(2027, 2, 15))
    assert any("overdue" in p for p in found)
    assert flags.problems(today=date(2027, 2, 14)) == []  # the day itself is still allowed


def test_a_flag_that_is_not_stable_needs_a_remove_by_within_90_days(flags, monkeypatch):
    def edit(r):
        next(f for f in r["flag"] if f["name"] == "ACTION_LOG").pop("remove_by")
        next(f for f in r["flag"] if f["name"] == "ECHO_FILTER_ENABLED")["remove_by"] = "2027-06-01"

    found = _with(flags, monkeypatch, edit)
    assert any("ACTION_LOG" in p and "needs a `remove_by`" in p for p in found)
    assert any("ECHO_FILTER_ENABLED" in p and "more than 90 days" in p for p in found)


def test_the_budget_can_only_be_met_not_exceeded(flags, monkeypatch):
    found = _with(flags, monkeypatch, lambda r: r.update(budget=len(r["flag"]) - 1))
    assert any("above the budget" in p for p in found)


def test_a_default_that_differs_from_the_code_fails(flags, monkeypatch):
    def edit(r):
        f = next(f for f in r["flag"] if f["name"] == "ECHO_FILTER_ENABLED")
        f["default"] = not f["default"]

    assert any("differs from the code default" in p for p in _with(flags, monkeypatch, edit))


def test_a_hand_edited_index_or_doc_fails(flags, monkeypatch, tmp_path):
    example = tmp_path / ".env.example"
    example.write_text(flags.EXAMPLE.read_text().replace("#   ACTION_LOG ", "#   ACTION_LOGX", 1))
    monkeypatch.setattr(flags, "EXAMPLE", example)
    assert any("index differs" in p for p in flags.problems(today=date(2026, 11, 16)))
    doc = tmp_path / "flags.md"
    doc.write_text("edited")
    monkeypatch.setattr(flags, "EXAMPLE", ROOT / "backend/.env.example")
    monkeypatch.setattr(flags, "FLAGS_DOC", doc)
    assert any("docs/flags.md differs" in p for p in flags.problems(today=date(2026, 11, 16)))
