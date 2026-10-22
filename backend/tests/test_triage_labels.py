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


def test_replay_re_decides_saved_classifier_answers_for_free(tmp_path):
    """A model answer saved in a report is re-decided without a call: Infant is measured on its shadow."""
    from superaide.evals.triage_report import Decision as D

    label = next(x for x in triage_report.load() if x.id == "tl-026")  # "the demo is in 20 minutes …"
    saved = {
        "output": {"category": "request", "urgency": "critical", "needs_owner": True, "can_answer": "no",
                   "evidence": ["the build is broken"], "confidence": 0.9},
        "agreement": None, "fallback": "", "deadline_at": None,
    }  # fmt: skip
    r = triage_report.decide(label, replay=saved)
    assert r.decision == D.HOLD  # at Infant the model alone can't interrupt
    assert r.shadow == D.INTERRUPT and r.judged == D.INTERRUPT and r.ok and r.cost == 0
    report = tmp_path / "saved.json"
    report.write_text(json.dumps({"per_label": {"tl-026": saved}}))
    s = triage_report.run(live=False, replay=report, out_dir=tmp_path, name="r")
    assert s["mode"] == "replay of saved.json" and s["cost_usd"] == 0
