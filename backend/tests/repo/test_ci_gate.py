"""The `CI result` gate treats the end-to-end suite as advisory on pull requests only (scripts/ci_gate.py)."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("ci_gate", ROOT / "scripts" / "ci_gate.py")
assert spec and spec.loader
ci_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_gate)

PR, PUSH, MQ, SCHED, MANUAL = "pull_request", "push", "merge_group", "workflow_call", "workflow_dispatch"
LABEL = "e2e-blocking"

# (event, e2e, e2e-smoke, labels, gate fails because of e2e?)
TABLE = [
    (PR, "success", "skipped", "", False),
    (PR, "failure", "skipped", "", False),  # advisory
    (PR, "cancelled", "skipped", "", False),  # advisory (also a timed-out shard)
    (PR, "skipped", "failure", "type:bug", False),  # smoke is advisory too
    (PR, "failure", "skipped", f"area:x,{LABEL}", True),  # label makes it block
    (PR, "cancelled", "skipped", LABEL, True),
    (PR, "success", "skipped", LABEL, False),
    (PR, "failure", "skipped", None, True),  # labels unreadable: fail closed
    (PR, "mystery", "skipped", "", True),  # unknown state is never forgiven
    (PR, "failure", "skipped", "e2e-blocking-not", False),  # exact label match only
    (PUSH, "failure", "skipped", "", True),
    (PUSH, "cancelled", "skipped", "", True),
    (MQ, "failure", "skipped", "", True),
    (SCHED, "failure", "skipped", "", True),
    (MANUAL, "failure", "skipped", "", True),
    (PUSH, "success", "skipped", "", False),
    (PUSH, "skipped", "success", "", False),
]


@pytest.mark.parametrize(("event", "e2e", "smoke", "labels", "fails"), TABLE)
def test_gate_decision_table(event, e2e, smoke, labels, fails):
    results = {"e2e": e2e, "e2e-smoke": smoke}
    assert bool(ci_gate.gate_failures(event, labels, results)) is fails
    forgiven = ci_gate.advisory_failed(event, labels, results)
    if e2e in ("failure", "cancelled") and not fails:
        assert forgiven == ["e2e"]
    if fails:
        assert not (set(forgiven) & set(ci_gate.gate_failures(event, labels, results)))


def test_cli_prints_the_verdict():
    out = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "ci_gate.py")],
        env={"EVENT": PR, "LABELS": "", "E2E_RESULTS": "e2e=failure e2e-smoke=skipped"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "blocking=false" in out and "gate_failures=\n" in out and "advisory_failed=e2e" in out


def _ci():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())


def test_gate_job_keeps_its_name_and_needs():
    jobs = _ci()["jobs"]
    assert jobs["result"]["name"] == "CI result"
    assert {
        "changes",
        "backend-static",
        "backend-tests",
        "frontend",
        "e2e",
        "e2e-smoke",
        "images",
        "secrets",
        "tests-required",
    } == set(jobs["result"]["needs"])
    for j in ("e2e", "e2e-smoke"):  # still run on every event
        assert jobs[j]["timeout-minutes"] <= 15


def test_gate_script_wires_label_and_everything_else_stays_blocking():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    run = _ci()["jobs"]["result"]["steps"][1]["run"]
    assert "scripts/ci_gate.py" in run and "e2e advisory on PRs:" in run
    # Only the two e2e jobs are excluded from the generic failure check.
    assert run.count('select(.key != "e2e" and .key != "e2e-smoke")') == 1
    # Everything else stays fail-closed.
    kept = ("changes secrets", "backend-static was required", "backend-tests was required", "frontend was required")
    for needle in (*kept, "images was required", "tests-required is"):
        assert needle in run, needle
    # Unreadable labels fail closed (LABELS=-).
    assert re.search(r"\|\| LABELS=-", run)
    assert "e2e-blocking" in text
