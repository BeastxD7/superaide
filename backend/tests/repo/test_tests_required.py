"""The CI rule 'feat and fix PRs ship tests' (scripts/tests_required.py)."""

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[3] / "scripts" / "tests_required.py"
_spec = importlib.util.spec_from_file_location("tests_required", _PATH)
assert _spec and _spec.loader
tr = importlib.util.module_from_spec(_spec)
sys.modules["tests_required"] = tr
_spec.loader.exec_module(tr)

SRC = "backend/src/superaide/pipeline.py"


def test_feat_with_tests_passes():
    assert tr.check("feat(triage): hold", [], [SRC, "backend/tests/test_x.py"])[0]
    assert tr.check("feat: ui", [], ["frontend/src/a.tsx", "frontend/src/a.test.tsx"])[0]
    assert tr.check("feat: flow", [], ["e2e/src/f.ts", "e2e/tests/f.spec.ts"])[0]


def test_feat_without_tests_fails_with_a_clear_message():
    ok, lines = tr.check("feat(x): thing", [], [SRC, "README.md"])
    assert not ok
    assert "::error::" in lines[0] and "tests-not-needed" in "\n".join(lines)


def test_fix_without_tests_fails_and_breaking_marker_is_parsed():
    assert not tr.check("fix(auth): bug", [], [SRC])[0]
    assert not tr.check("feat(api)!: break", [], ["frontend/src/x.ts"])[0]


def test_docs_and_other_types_are_not_checked():
    for t in ("docs", "chore", "ci", "build", "refactor", "perf", "test", "style"):
        assert tr.check(f"{t}: x", [], [SRC])[0]


def test_label_exempts():
    assert tr.check("feat: x", ["tests-not-needed"], [SRC])[0]


def test_feat_with_only_docs_files_passes():
    assert tr.check("feat: docs only", [], ["docs/a.md", "README.md", "scripts/x.py"])[0]


def test_title_without_prefix_passes_with_a_warning():
    ok, lines = tr.check("Update stuff", [], [SRC])
    assert ok and lines[0].startswith("::warning::")


def test_a_frontend_test_file_alone_is_not_source():
    assert tr.check("feat: x", [], ["frontend/src/a.test.ts"])[0]
