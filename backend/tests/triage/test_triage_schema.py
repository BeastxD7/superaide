"""ADR 0010 TR0: the triage tables, their defaults, and the classifier contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from superaide.db import rls
from superaide.db.models import Base, Person
from superaide.db.session import session_scope
from superaide.triage.models import TriageSettings
from superaide.triage.types import DEFAULTS, TriageOutput

TRIAGE_TABLES = {"triage_settings", "triage_decision", "escalation", "escalation_delivery", "escalation_ack",
                 "triage_rule"}  # fmt: skip


def test_every_triage_table_is_per_instance_and_under_rls():
    for name in TRIAGE_TABLES:
        table = Base.metadata.tables[name]
        assert {"agent_id", "tenant_id"} <= set(table.columns.keys()), name
        assert name in rls.TABLES, name


def test_settings_start_with_the_proposed_defaults():
    with session_scope() as s:
        p = Person(display_name="Owner", relation="manager")
        s.add(p)
        s.flush()
        row = TriageSettings(person_id=p.id)
        s.add(row)
        s.flush()
        row_id = row.id
    with session_scope() as s:
        got = s.get(TriageSettings, row_id)
        assert got is not None
        assert got.ladder == DEFAULTS["ladder"] and got.ladder is not DEFAULTS["ladder"]
        assert (got.max_per_day, got.max_per_hour, got.theta_interrupt) == (6, 2, 0.6)
        assert got.away_visibility == "colleagues" and got.stranger_intake is False
        assert got.silenced_people == [] and got.autonomy_grants == []


def _out(**kw):
    base = {"category": "request", "urgency": "high", "needs_owner": True, "can_answer": "no", "confidence": 0.8}
    return TriageOutput.model_validate({**base, **kw})


def test_classifier_output_is_strict():
    assert _out().urgency == "high"
    with pytest.raises(ValidationError):
        _out(confidence=1.4)
    with pytest.raises(ValidationError):
        _out(evidence=["a", "b", "c", "d"])
    with pytest.raises(ValidationError):
        _out(mood="angry")
    with pytest.raises(ValidationError):
        _out(risk_flags=["vibes"])
    assert len(_out(reason="x" * 500).reason) == 200


def test_the_schema_sent_to_the_model_has_no_numeric_ranges():
    schema = TriageOutput.model_json_schema()
    assert schema.get("additionalProperties") is False
    for prop in schema["properties"].values():
        assert not {"minimum", "maximum", "maxItems", "maxLength"} & set(prop), prop
