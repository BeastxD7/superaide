"""The editable-surface manifest (issue #114): nothing that learns can touch what judges it."""

from __future__ import annotations

import json
import os
import random
import re
import warnings
from pathlib import Path

import pytest

from superaide.platform.config import get_settings
from superaide.platform.db.models import Base
from superaide.platform.security import surface

REPO = surface.REPO


def test_the_protected_files_match_the_lock():
    """Advisory by default (a warning), so parallel PRs are not blocked by each other's lock refresh. Set
    SURFACE_LOCK_STRICT=1 (CI for release branches) to make a difference a failure; fix it with
    `python -m superaide.platform.security.surface --update`, which CODEOWNERS makes a human review."""
    problems = surface.differences()
    if problems and os.environ.get("SURFACE_LOCK_STRICT") == "1":
        pytest.fail("; ".join(problems))
    if problems:
        warnings.warn(f"surface.lock.json is out of date: {'; '.join(problems[:3])}", stacklevel=1)


def test_a_changed_protected_file_is_caught_and_a_new_one_too(tmp_path, monkeypatch):
    root = tmp_path
    for rel in [*surface.protected_files(), "backend/src/superaide/platform/security/surface.toml"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes((REPO / rel).read_bytes())
    lock = tmp_path / "lock.json"  # a lock made for this copy, so the real lock's freshness doesn't matter here
    lock.write_text(json.dumps(surface.current(root)))
    monkeypatch.setattr(surface, "LOCK", lock)
    assert surface.differences(root) == []
    (root / "backend/src/superaide/policy/actions.py").write_text("# the optimiser was here\n")
    (root / "backend/src/superaide/policy/extra.py").write_text("x = 1\n")
    (root / "backend/src/superaide/platform/security/surface.toml").write_text("# widened\n")
    got = surface.differences(root)
    assert "changed: backend/src/superaide/policy/actions.py" in got
    assert "new protected file: backend/src/superaide/policy/extra.py" in got
    assert "changed: backend/src/superaide/platform/security/surface.toml" in got


def test_the_manifest_itself_is_locked():
    assert "backend/src/superaide/platform/security/surface.toml" in surface.current()


def test_writable_and_protected_never_overlap_and_tables_exist():
    sf = surface.load()
    assert not sf.writable_tables & sf.protected_tables
    assert (sf.writable_tables | sf.protected_tables) <= set(Base.metadata.tables) | {"memory_embedding"}
    for p in sf.writable_paths:
        assert not surface.check_edit([p.replace("*", "x")], surface=sf) or "protected" not in "".join(
            surface.check_edit([p.replace("*", "x")], surface=sf)
        )


def test_prompts_and_skills_are_editable_and_graders_are_not():
    assert surface.check_edit(["backend/src/superaide/brain/charter.md"]) == []
    assert surface.check_edit(["backend/src/superaide/skills/builtin/memory/SKILL.md"]) == []
    for bad in [
        "backend/src/superaide/context/audience.py",
        "backend/src/superaide/platform/db/rls.py",
        "backend/evals/scenarios/x.yaml",
        "backend/tests/redteam/test_canaries.py",
        "backend/src/superaide/policy/actions.py",
        "backend/src/superaide/platform/security/surface.toml",
        "backend/src/superaide/platform/security/surface.lock.json",
        "backend/src/superaide/brain/budget.py",
        "backend/src/superaide/evals/harness.py",
    ]:
        assert surface.check_edit([bad]), bad
    assert surface.check_edit([], ["approval"]) and surface.check_edit([], ["memory_item"]) == []
    assert surface.check_edit([], ["something_new"])  # unknown tables are refused


def test_traversal_and_lookalike_paths_are_refused():
    for bad in [
        "backend/src/superaide/brain/../policy/actions.py",
        "../backend/src/superaide/policy/actions.py",
        "/backend/src/superaide/brain/charter.md",
        "~/backend/src/superaide/brain/charter.md",
        "backend/src/superaide/brain/charter.md.bak",
        "backend/src/superaide/policy",
        "backend\\src\\superaide\\policy\\actions.py",
    ]:
        assert surface.check_edit([bad]), bad


def test_100_synthetic_self_edits_none_merge_into_a_protected_path():
    """The smallest experiment from the issue: edits touching a protected path merged = 0."""
    rng = random.Random(114)
    sf = surface.load()
    pool = surface.protected_files() + [
        "backend/src/superaide/brain/charter.md",
        "backend/src/superaide/skills/builtin/memory/SKILL.md",
        "backend/src/superaide/brain/turn.py",
        "backend/src/superaide/knowledge/store.py",
        "frontend/src/proxy.ts",
    ]
    merged_protected = refused = 0
    for _ in range(100):
        edit = rng.sample(pool, rng.randint(1, 3))
        if rng.random() < 0.3:
            edit.append(edit[0].replace("/superaide/", "/superaide/./"))
        problems = surface.check_edit(edit)
        touches = any(any(surface._match(surface._norm(e), g) for g in sf.protected_paths) for e in edit)
        if not problems and touches:
            merged_protected += 1
        refused += bool(problems)
    assert merged_protected == 0 and refused > 50


def test_boot_check_is_off_by_default_and_refuses_on_a_difference(monkeypatch):
    assert get_settings().surface_check_on_boot is False
    monkeypatch.setattr(surface, "differences", lambda **kw: [])  # a clean tree boots
    surface.verify_boot()
    monkeypatch.setattr(surface, "differences", lambda **kw: ["changed: backend/src/superaide/policy/actions.py"])
    with pytest.raises(RuntimeError, match="protected file changed"):
        surface.verify_boot()


def test_codeowners_covers_every_protected_folder():
    owners = (REPO / ".github" / "CODEOWNERS").read_text()
    covered = [line.split()[0] for line in owners.splitlines() if line.startswith("/")]
    for pattern in surface.load().protected_paths:
        folder = "/" + re.sub(r"/\*\*$", "/", pattern)
        assert any(folder.startswith(c) for c in covered), pattern
    assert json.loads(Path(surface.LOCK).read_text())
