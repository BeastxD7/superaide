"""The dependency policy (issue #418, docs/dependencies.md): grouped weekly Dependabot, a doc that names the riskiest
dependencies, a canary that only warns, and a reason next to every exact pin."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_dependency_policy.py"


@pytest.fixture
def policy():
    spec = importlib.util.spec_from_file_location("check_dependency_policy", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_repository_follows_the_policy(policy):
    assert policy.problems() == []


def test_an_ungrouped_or_unscheduled_ecosystem_fails(policy, tmp_path, monkeypatch):
    f = tmp_path / "dependabot.yml"
    f.write_text(
        "version: 2\nupdates:\n"
        "  - package-ecosystem: npm\n    directory: /frontend\n    schedule:\n      interval: weekly\n"
        "  - package-ecosystem: docker\n    directory: /backend\n    schedule:\n      interval: daily\n"
        "    groups:\n      x:\n        patterns: ['*']\n"
    )
    monkeypatch.setattr(policy, "DEPENDABOT", f)
    found = policy.dependabot_problems()
    assert any("npm has no `groups:`" in p for p in found)
    assert any("docker is not scheduled weekly" in p for p in found)


def test_a_stale_row_in_the_riskiest_table_fails(policy, tmp_path, monkeypatch):
    doc = tmp_path / "dependencies.md"
    doc.write_text((ROOT / "docs/dependencies.md").read_text().replace("`psycopg`", "`psycopg9`"))
    monkeypatch.setattr(policy, "DOC", doc)
    assert any("does not name `psycopg`" in p for p in policy.doc_problems())


def test_a_dependency_removed_from_the_manifest_fails(policy, tmp_path, monkeypatch):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('dependencies = [\n    "anthropic>=1.17.0",\n]\n')
    monkeypatch.setattr(policy, "PYPROJECT", manifest)
    found = policy.doc_problems()
    assert any("`psycopg` but pyproject no longer depends" in p for p in found)
    assert not any("`anthropic`" in p for p in found)


def test_a_blocking_canary_job_fails(policy, tmp_path, monkeypatch):
    f = tmp_path / "canary.yml"
    f.write_text((ROOT / ".github/workflows/canary.yml").read_text().replace("    continue-on-error: true\n", ""))
    monkeypatch.setattr(policy, "CANARY", f)
    found = policy.canary_problems()
    assert any("python-next must be `continue-on-error: true`" in p for p in found)
    assert any("node-next must be `continue-on-error: true`" in p for p in found)


def test_an_exact_pin_without_a_reason_fails(policy, tmp_path, monkeypatch):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('dependencies = [\n    "foo==1.0",\n    "bar==2.0",  # why: it downloads models\n]\n')
    monkeypatch.setattr(policy, "PYPROJECT", manifest)
    found = policy.pin_problems()
    assert len(found) == 1 and "foo==1.0" in found[0]
