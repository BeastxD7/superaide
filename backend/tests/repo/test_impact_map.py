"""scripts/impact_map.py (issue #520): the test impact map in shadow mode. It never decides what CI runs; these tests
pin what it proposes, and above all when it must refuse to propose anything narrower than the real selection."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[3]
STORE = "backend/src/superaide/memory/store.py"
SLEEP_TEST = "backend/tests/memory/test_sleep.py"  # a test of the module that owns STORE
FAR_TEST = "backend/tests/channels/test_channel_format.py"  # what the map says also runs STORE
NOT_SELECTED = "backend/tests/app/api/test_episodes.py"  # only reaches STORE through importers: the map drops it


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def im():
    return load("impact_map_under_test", "scripts/impact_map.py")


@pytest.fixture(scope="module")
def idx(im):
    st = im.load_selector(ROOT)
    return st.build_index(ROOT)


def fresh_map(**over: Any) -> dict[str, Any]:
    m = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": "abc",
        "files": {STORE: [FAR_TEST]},
        "tests": [FAR_TEST],
    }
    return {**m, **over}


def test_a_change_runs_its_own_module_the_tests_the_map_lists_and_the_guards(im, idx) -> None:
    got = im.propose([STORE], idx, fresh_map())
    files = {"backend/" + f for f in got["backend_files"]}
    assert got["mode"] == "impact"
    assert SLEEP_TEST in files  # every test of the module that was changed
    assert FAR_TEST in files  # the test the map says really ran the file
    assert "backend/tests/console/test_main.py" in files  # the composition smoke
    assert NOT_SELECTED not in files and len(files) < len(got["real_backend"])  # dependents' whole suites are not run


@pytest.mark.parametrize(
    ("impact", "why"),
    [
        (None, "no usable impact map"),
        ({"version": 1, "generated_at": "", "files": {}}, "no usable impact map"),
        ({"version": 99, "generated_at": "2026-01-01T00:00:00+00:00", "files": {STORE: []}}, "no usable impact map"),
        (fresh_map(generated_at="not a date"), "no date"),
        (fresh_map(generated_at=(datetime.now(UTC) - timedelta(days=8)).isoformat()), "older than 7 days"),
        (fresh_map(files={"backend/src/superaide/memory/audience.py": [FAR_TEST]}), "does not know memory/store.py"),
    ],
)
def test_a_map_that_cannot_be_trusted_falls_back_to_the_real_selection(im, idx, impact, why) -> None:
    got = im.propose([STORE], idx, impact)
    assert got["mode"] == "fallback" and why in got["reason"]
    assert got["backend_files"] == got["real_backend"]  # never narrower than the real rule


@pytest.mark.parametrize(
    "path",
    [
        "backend/tests/api_support.py",  # a test helper: who imports it is a different question
        "backend/src/superaide/channels/slack/manifest.yml",  # not Python, so not in the map
    ],
)
def test_changes_the_map_does_not_describe_fall_back(im, idx, path: str) -> None:
    got = im.propose([STORE, path], idx, fresh_map())
    assert got["mode"] == "fallback" and got["backend_files"] == got["real_backend"]


def test_docs_and_shared_files_are_left_to_the_real_selector(im, idx) -> None:
    assert im.propose(["README.md"], idx, fresh_map())["mode"] == "none"
    full = im.propose(["backend/pyproject.toml"], idx, fresh_map())
    assert full["mode"] == "full" and full["backend_files"] == full["real_backend"]


def test_a_changed_test_always_runs(im, idx) -> None:
    got = im.propose([STORE, NOT_SELECTED], idx, fresh_map())
    assert got["mode"] == "impact" and NOT_SELECTED.removeprefix("backend/") in got["backend_files"]


def test_a_map_entry_for_a_deleted_test_is_ignored(im, idx) -> None:
    got = im.propose([STORE], idx, fresh_map(files={STORE: ["backend/tests/gone/test_gone.py", FAR_TEST]}))
    assert (
        "tests/gone/test_gone.py" not in got["backend_files"]
        and "tests/channels/test_channel_format.py" in got["backend_files"]
    )


def test_the_shards_of_one_run_merge_and_the_oldest_date_wins(im) -> None:
    a = {"generated_at": "2026-10-27T10:00:00+00:00", "commit": "x", "files": {"f": ["t1"]}, "tests": ["t1"]}
    b = {
        "generated_at": "2026-10-27T08:00:00+00:00",
        "commit": "x",
        "files": {"f": ["t2"], "g": ["t3"]},
        "tests": ["t2", "t3"],
    }
    got = im.merge([a, b])
    assert got["files"] == {"f": ["t1", "t2"], "g": ["t3"]} and got["generated_at"] == b["generated_at"]
    assert got["tests"] == ["t1", "t2", "t3"]


def make_data(tmp_path: Path) -> str:
    data_file = str(tmp_path / "cov.data")
    data = CoverageData(basename=data_file)
    src = "/work/backend/src/superaide/memory/store.py"
    other = "/work/backend/src/superaide/policy/actions.py"
    data.set_context("tests/memory/test_a.py::test_one|run")
    data.add_lines({src: [1, 2]})
    data.set_context("tests/policy/test_b.py::test_two|setup")  # a fixture counts: it is part of the test
    data.add_lines({src: [3], other: [9]})
    data.set_context("")  # import time belongs to no test
    data.add_lines({other: [1]})
    data.write()
    return data_file


def test_the_map_is_built_from_coverage_contexts(im, tmp_path: Path) -> None:
    got = im.build(make_data(tmp_path), "abc")
    assert got["files"][STORE] == ["backend/tests/memory/test_a.py", "backend/tests/policy/test_b.py"]
    assert got["files"]["backend/src/superaide/policy/actions.py"] == ["backend/tests/policy/test_b.py"]
    assert (
        got["tests"] == ["backend/tests/memory/test_a.py", "backend/tests/policy/test_b.py"] and got["commit"] == "abc"
    )


def test_drift_counts_what_a_fresh_run_learned_that_the_old_map_did_not_know(im, tmp_path: Path) -> None:
    fresh = im.executed_by(make_data(tmp_path))
    old = {"files": {STORE: ["backend/tests/memory/test_a.py"]}}
    got = im.drift(old, fresh)
    assert got["pairs"] == 3 and got["new_pairs"] == 2 and got["files_with_new"] == 2
    assert "2 of 3" in im.render_drift(got)


def test_the_shadow_report_compares_and_never_fails_the_build(im, idx, tmp_path: Path, capsys) -> None:
    report = im.render_shadow(im.propose([STORE], idx, fresh_map()), len(idx.backend_tests))
    assert "nothing here changes what runs" in report and "Under the impact map it would run" in report
    fallback = im.render_shadow(im.propose([STORE], idx, None), len(idx.backend_tests))
    assert "Falls back to the real selection" in fallback
    summary = tmp_path / "summary.md"
    assert (
        im.main(["shadow", "--map", str(tmp_path / "missing.json"), "--changed", STORE, "--summary-file", str(summary)])
        == 0
    )
    assert "Falls back" in summary.read_text()
    assert (
        im.main(["shadow", "--map", "x", "--changed", STORE, "--root", str(tmp_path / "not-a-repo")]) == 0
    )  # crash: still 0
    assert "skipped" in capsys.readouterr().out
    (tmp_path / "bad.json").write_text("{not json")
    assert im.main(["shadow", "--map", str(tmp_path / "bad.json"), "--changed", STORE]) == 0


def test_the_command_line_builds_merges_and_reads_a_map(im, tmp_path: Path) -> None:
    out = tmp_path / "a.json"
    assert im.main(["build", "--data", make_data(tmp_path), "--out", str(out), "--commit", "c"]) == 0
    merged = tmp_path / "m.json"
    assert im.main(["merge", str(out), str(tmp_path / "missing.json"), "--out", str(merged)]) == 0
    assert json.loads(merged.read_text())["files"][STORE]


def test_the_drift_command_reports_a_first_run_without_a_previous_map(im, tmp_path: Path, capsys) -> None:
    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps({"files": {STORE: ["backend/tests/memory/test_a.py"]}}))
    assert im.main(["drift", "--map", str(tmp_path / "missing.json"), "--fresh", str(fresh)]) == 0
    assert "no previous map" in capsys.readouterr().out
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"files": {STORE: []}}))
    assert im.main(["drift", "--map", str(old), "--fresh", str(fresh)]) == 0
    assert "1 of 1" in capsys.readouterr().out
