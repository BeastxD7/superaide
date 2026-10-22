"""ADR 0010 TR10: the labelled triage set and `python -m superaide.evals triage` (offline, free)."""

from __future__ import annotations

import json

from superaide.evals import triage_report
from superaide.evals.__main__ import main
from superaide.triage.types import Decision


def test_the_labels_are_synthetic_unique_and_enough():
    labels = triage_report.load()
    assert len(labels) >= 50
    assert all(x.synthetic for x in labels)
    assert {x.expected for x in labels} >= {Decision.HOLD, Decision.INTERRUPT, Decision.VERIFY}


def test_rules_only_never_cry_wolf_and_catch_every_risky_request():
    results = [triage_report.decide(x) for x in triage_report.load()]
    s = triage_report.summarise(results, mode="offline")
    assert s["cost_usd"] == 0
    assert s["tell_now"]["precision"] == 1.0  # rules alone never interrupt for nothing
    assert s["verify_recall"] == 1.0
    assert s["acceptable"] >= 0.9


def test_the_report_command_writes_markdown_and_json(tmp_path, capsys):
    assert main(["triage", "--out", str(tmp_path), "--name", "t"]) == 0
    data = json.loads((tmp_path / "t.json").read_text())
    assert data["labels"] >= 50 and data["mode"].startswith("offline")
    md = (tmp_path / "t.md").read_text()
    assert "Tell-now precision" in md and "| verify |" in md
    assert "tell-now precision" in capsys.readouterr().out
