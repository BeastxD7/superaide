"""The opt-in outside-system baseline for the memory evals (#51): the logic, and the promises about Mem0."""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path

import pytest

from superaide.evals import baseline
from superaide.evals.gate import ltm_scenarios

BACKEND = Path(__file__).resolve().parents[1]
BY_ID = {s.id: s for s in ltm_scenarios()}


class WordOverlap:
    """A stand-in outside system: ranks notes by how many query words they share."""

    name = "Word overlap"

    def __init__(self) -> None:
        self.notes: list[str] = []

    def reset(self) -> None:
        self.notes = []

    def add(self, notes: list[str]) -> None:
        self.notes += notes

    def search(self, query: str, k: int) -> list[str]:
        words = set(query.lower().split())
        ranked = sorted(self.notes, key=lambda n: -len(words & set(re.findall(r"\w+", n.lower()))))
        return ranked[:k]


def test_only_read_only_owner_questions_are_compared():
    assert baseline.owner_cases(BY_ID["ltm-update-city"]) == []  # it teaches Ava in its turns
    assert baseline.owner_cases(BY_ID["ltm-update-role"]) == []  # so does a correction
    cases = baseline.owner_cases(BY_ID["ltm-multi-two-facts-one-question"])
    assert cases and all(c.contains for c in cases)
    tiers = baseline.owner_cases(BY_ID["ltm-canary-tiers-retrieval"])
    assert all(c.scenario == "ltm-canary-tiers-retrieval" for c in tiers)  # colleague and group checks are skipped


def test_grading_needs_every_expected_pattern_in_the_top_k_and_none_of_the_absent_ones():
    case = baseline.Case("s", "q", ("alpha", "beta"), ("secret",), 2)
    assert baseline.grade(case, ["alpha one", "beta two", "gamma"])
    assert not baseline.grade(case, ["alpha one", "gamma", "beta two"])  # beta is outside the top 2
    assert not baseline.grade(case, ["alpha secret", "beta"])  # something absent was served


def test_compare_loads_the_same_notes_into_the_other_system_and_renders_a_table():
    sc = BY_ID["ltm-multi-two-facts-one-question"]
    other = WordOverlap()
    rows = baseline.compare([sc], other)
    assert rows and other.notes  # the scenario's seeded notes (with filler) reached the other system
    text = baseline.render(rows, other.name)
    assert f"/{len(rows)}**" in text and "| ltm-multi-two-facts-one-question |" in text
    assert "no audience gate" in text


def test_results_text_reads_the_shapes_outside_systems_answer_in():
    assert baseline.results_text({"results": [{"memory": "a"}, {"text": "b"}]}) == ["a", "b"]
    assert baseline.results_text(["x", {"memory": "y"}]) == ["x", "y"]


def _script():
    spec = importlib.util.spec_from_file_location("mem0_baseline", BACKEND / "evals" / "baselines" / "mem0_baseline.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_the_script_refuses_to_run_in_ci(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(SystemExit, match="never runs in CI"):
        _script().main([])


def test_the_script_says_how_to_install_mem0_when_it_is_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setitem(sys.modules, "mem0", None)  # an import of it now fails, installed or not
    with pytest.raises(SystemExit, match="uv sync --extra eval-mem0"):
        _script().main([])


def test_mem0_is_only_an_optional_extra_and_the_app_never_imports_it():
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    assert any(d.startswith("mem0ai==") for d in project["project"]["optional-dependencies"]["eval-mem0"])
    default = project["project"]["dependencies"] + [d for g in project["dependency-groups"].values() for d in g]
    assert not any("mem0" in d for d in default)
    for path in (BACKEND / "src").rglob("*.py"):
        assert not re.search(r"^\s*(import|from)\s+mem0", path.read_text(), re.M), path


def test_ci_never_runs_the_baseline():
    root = BACKEND.parent
    for wf in (root / ".github" / "workflows").glob("*.y*ml"):
        text = wf.read_text()
        assert "mem0" not in text.lower(), wf
