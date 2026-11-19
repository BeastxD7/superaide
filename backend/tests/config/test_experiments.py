"""#405: experiments use an EXPERIMENT_ prefix, accept the old name for one release and warn, and carry a hypothesis,
a metric and a sunset date in the registry."""

import importlib.util
import logging
from datetime import date
from pathlib import Path

import pytest

from superaide.platform import renamed
from superaide.platform.config import Settings

ROOT = Path(__file__).resolve().parents[3]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def flags():
    return _script("check_flags")


@pytest.fixture
def experiments():
    return _script("check_experiments")


FIELD_OF = {old: new.removeprefix("EXPERIMENT_").lower() + "_enabled" for old, new in renamed.RENAMED.items()}


def _clean(monkeypatch):
    for old, new in renamed.RENAMED.items():
        monkeypatch.delenv(old, raising=False)
        monkeypatch.delenv(new, raising=False)


@pytest.mark.parametrize("old,new", sorted(renamed.RENAMED.items()))
def test_the_new_name_turns_an_experiment_on_and_so_does_the_old_one(monkeypatch, old, new):
    _clean(monkeypatch)
    field = FIELD_OF[old]
    assert getattr(Settings(_env_file=None), field) is False
    monkeypatch.setenv(new, "true")
    assert getattr(Settings(_env_file=None), field) is True
    monkeypatch.delenv(new)
    monkeypatch.setenv(old, "true")
    assert getattr(Settings(_env_file=None), field) is True


def test_the_new_name_wins_when_both_are_set(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("MIRROR_ENABLED", "true")
    monkeypatch.setenv("EXPERIMENT_MIRROR", "false")
    assert Settings(_env_file=None).mirror_enabled is False


def test_an_old_name_is_reported_once_at_startup(monkeypatch, caplog, tmp_path):
    with caplog.at_level(logging.WARNING, logger=renamed.log.name):
        found = renamed.warn({"AUDITION_ENABLED": "true", "MAX_SPEND_USD": "5"}, env_file=tmp_path / "none")
    assert found == ["AUDITION_ENABLED"]
    assert [r.message for r in caplog.records if "renamed EXPERIMENT_AUDITION" in r.message]
    f = tmp_path / ".env"
    f.write_text("# MIRROR_ENABLED=true\nEXPERIMENT_MIRROR=true\n")
    assert renamed.old_names_in_use({}, env_file=f) == []  # a comment and the new name are fine


def test_every_experiment_in_the_registry_has_a_hypothesis_metric_and_sunset(flags):
    exps = [f for f in flags.load()["flag"] if f["name"].startswith("EXPERIMENT_")]
    assert len(exps) == len(renamed.RENAMED)
    for f in exps:
        assert f["hypothesis"].strip() and f["metric"].strip() and f["sunset"]


@pytest.mark.parametrize("missing", ["hypothesis", "metric", "sunset"])
def test_an_experiment_without_a_hypothesis_metric_or_sunset_fails(flags, monkeypatch, missing):
    reg = flags.load()
    exp = next(f for f in reg["flag"] if f["name"] == "EXPERIMENT_MIRROR")
    del exp[missing]
    monkeypatch.setattr(flags, "load", lambda: reg)
    found = flags.problems(today=date(2026, 11, 16))
    assert any(f"experiment EXPERIMENT_MIRROR has no {missing}" in p for p in found)


def test_an_experiment_must_be_off_by_default_and_in_the_experiment_group(flags, monkeypatch):
    reg = flags.load()
    exp = next(f for f in reg["flag"] if f["name"] == "EXPERIMENT_MIRROR")
    exp["group"] = "owner-safe"
    monkeypatch.setattr(flags, "load", lambda: reg)
    assert any("must be in the experiment group" in p for p in flags.problems(today=date(2026, 11, 16)))


def test_a_sunset_more_than_180_days_after_added_fails(flags, monkeypatch):
    reg = flags.load()
    next(f for f in reg["flag"] if f["name"] == "EXPERIMENT_MIRROR")["sunset"] = "2028-01-01"
    monkeypatch.setattr(flags, "load", lambda: reg)
    assert any("more than 180 days" in p for p in flags.problems(today=date(2026, 11, 16)))


def test_a_flag_in_the_experiment_group_needs_the_prefix(flags, monkeypatch):
    reg = flags.load()
    next(f for f in reg["flag"] if f["name"] == "ASK_LINK_ENABLED")["group"] = "experiment"
    monkeypatch.setattr(flags, "load", lambda: reg)
    assert any("has no EXPERIMENT_ prefix" in p for p in flags.problems(today=date(2026, 11, 16)))


def test_an_overdue_sunset_without_a_result_is_listed_and_one_with_a_result_is_not(experiments):
    reg = {
        "flag": [
            {"name": "EXPERIMENT_A", "sunset": "2027-02-14", "hypothesis": "h", "metric": "m"},
            {"name": "EXPERIMENT_B", "sunset": "2027-02-14", "hypothesis": "h", "metric": "m", "result": "kept: 40%"},
            {"name": "EXPERIMENT_C", "sunset": "2027-03-01", "hypothesis": "h", "metric": "m"},
            {"name": "ASK_LINK_ENABLED", "remove_by": "2020-01-01"},
        ]
    }
    due = experiments.overdue(reg, today=date(2027, 2, 15))
    assert [f["name"] for f in due] == ["EXPERIMENT_A"]
    assert experiments.overdue(reg, today=date(2027, 2, 14)) == []  # the sunset day itself is not overdue
