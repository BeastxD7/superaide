"""The decision journal (/app/journal): opt-in, the owner's own words, plain-arithmetic calibration, erasable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.brain.llm import FakeLLM
from superaide.config import get_settings
from superaide.db.models import Job, Prediction
from superaide.db.session import session_scope
from superaide.jobs.handlers import load
from superaide.jobs.queue import run_due
from superaide.journal import extract
from tests.api_support import OTHER, hire, signup

load()
OWN = "I'll ship the beta by 5 Nov, I'm 90% sure."


@pytest.fixture(autouse=True)
def many_owners_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)


@pytest.fixture
def scripted(agent, monkeypatch):
    """A Claude key is 'set' and the model proposes two predictions: one quoted from the owner, one invented."""
    monkeypatch.setattr("superaide.brain.wiring.brain_ready", lambda settings: True)

    def responder(purpose, system, messages, output_model):
        if purpose == "journal_extract":
            return extract.CandidateList(
                predictions=[
                    extract.Candidate(
                        statement="The beta ships by 5 November.",
                        quote="I'll ship the beta by 5 Nov",
                        confidence_pct=90,
                        inferred=False,
                        due_on="2026-11-05",
                        topic="timeline",
                    ),
                    extract.Candidate(
                        statement="The client will say yes.",
                        quote="the client will certainly say yes",  # not in the owner's message
                        confidence_pct=80,
                        inferred=True,
                        topic="people",
                    ),
                ]
            )
        return "Noted."

    monkeypatch.setattr("superaide.brain.wiring.build_llm", lambda settings: FakeLLM(responder))
    agent.llm = FakeLLM(responder)


def turn_on(owner) -> None:
    assert owner.post("/app/journal/settings", json={"enabled": True}).json()["enabled"] is True


def add(owner, statement="The beta ships by 5 Nov.", pct=90, **extra):
    return owner.post("/app/journal/predictions", json={"statement": statement, "confidence_pct": pct, **extra})


def test_it_is_off_until_the_owner_turns_it_on(owner):
    j = owner.get("/app/journal").json()
    assert j == {"enabled": False, "predictions": [], "calibration": None, "resolved": 0, "needed": 10}
    assert add(owner).status_code == 409


def test_chat_is_read_only_while_on_and_only_keeps_the_owners_own_words(owner, scripted):
    owner.post("/app/chat", json={"text": OWN})
    with session_scope() as s:
        assert s.scalar(select(Job).where(Job.kind == "journal_from_turn")) is None  # off: nothing is even queued
    turn_on(owner)
    owner.post("/app/chat", json={"text": OWN})
    run_due()
    preds = owner.get("/app/journal").json()["predictions"]
    assert [p["statement"] for p in preds] == ["The beta ships by 5 November."]  # the invented quote was dropped
    p = preds[0]
    assert p["quote"] == "I'll ship the beta by 5 Nov" and p["confidence_pct"] == 90 and p["source"] == "chat"
    assert p["due_on"] == "2026-11-05" and p["status"] == "open" and p["topic"] == "timeline"
    owner.post("/app/chat", json={"text": OWN})  # said again: not a second prediction
    run_due()
    assert len(owner.get("/app/journal").json()["predictions"]) == 1


def test_switching_off_before_the_job_runs_keeps_nothing(owner, scripted):
    turn_on(owner)
    owner.post("/app/chat", json={"text": OWN})
    owner.post("/app/journal/settings", json={"enabled": False})
    run_due()
    with session_scope() as s:
        assert s.scalars(select(Prediction)).all() == []


def test_turning_it_off_deletes_the_journal(owner):
    turn_on(owner)
    for i in range(3):
        assert add(owner, statement=f"Prediction number {i}").status_code == 200
    r = owner.post("/app/journal/settings", json={"enabled": False})
    assert r.json() == {"enabled": False, "deleted": 3}
    with session_scope() as s:
        assert s.scalars(select(Prediction)).all() == []
    assert owner.get("/app/journal").json()["predictions"] == []


def test_resolve_calibrate_and_coach_after_ten(owner):
    turn_on(owner)
    ids = []
    for i in range(10):
        r = add(owner, statement=f"Deadline number {i} will be met.", pct=90, topic="timeline")
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    assert owner.get("/app/journal").json()["calibration"] is None
    for i, pid in enumerate(ids):
        r = owner.post(f"/app/journal/predictions/{pid}/resolve", json={"came_true": i < 5, "note": "it slipped"})
        assert r.status_code == 200 and r.json()["status"] == "resolved"
        if i == 8:
            assert owner.get("/app/journal").json()["calibration"] is None  # nine is not enough
    j = owner.get("/app/journal").json()
    cal = j["calibration"]
    assert j["resolved"] == 10 and cal["resolved"] == 10 and cal["right"] == 5 and cal["hit_rate"] == 0.5
    assert cal["brier"] == 0.41 and cal["buckets"][0]["from"] == 90
    assert "On timelines you're right 50%" in cal["coaching"][0]
    assert owner.post(f"/app/journal/predictions/{ids[0]}/resolve", json={"came_true": True}).status_code == 404


def test_due_predictions_are_flagged(owner):
    turn_on(owner)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    add(owner, statement="Past due thing.", due_on=yesterday)
    add(owner, statement="Future thing.", due_on=tomorrow)
    by = {p["statement"]: p for p in owner.get("/app/journal").json()["predictions"]}
    assert by["Past due thing."]["due"] is True and by["Future thing."]["due"] is False


def test_input_is_validated_and_duplicates_refused(owner):
    turn_on(owner)
    assert add(owner, pct=49).status_code == 422 and add(owner, pct=100).status_code == 422
    assert add(owner, statement="x").status_code == 422
    assert add(owner, due_on="not-a-date").status_code == 422
    assert add(owner).status_code == 200 and add(owner).status_code == 409
    assert add(owner, statement="Odd topic.", topic="weird").json()["topic"] == "other"


def test_nobody_else_can_resolve_or_erase_my_predictions(browser, owner):
    turn_on(owner)
    pid = add(owner).json()["id"]
    other = browser()
    assert signup(other, **OTHER).status_code == 200 and hire(other).status_code == 200
    turn_on(other)
    assert other.post(f"/app/journal/predictions/{pid}/resolve", json={"came_true": True}).status_code == 404
    assert other.post(f"/app/journal/predictions/{pid}/erase").status_code == 404
    assert other.get("/app/journal").json()["predictions"] == []
    assert owner.post(f"/app/journal/predictions/{uuid.uuid4()}/erase").status_code == 404
    assert owner.post(f"/app/journal/predictions/{pid}/erase").json() == {"status": "erased"}
    assert owner.get("/app/journal").json()["predictions"] == []


def test_a_third_partys_message_is_never_read(owner, scripted, agent):
    """The job only reads inbound messages the owner themself sent."""
    from superaide.db.models import Event, Person

    turn_on(owner)
    with session_scope() as s:
        someone = Person(display_name="Priya Tan", relation="contact")
        s.add(someone)
        s.flush()
        ctx_id = s.scalar(select(Event.context_id).limit(1))
        ev = Event(
            context_id=ctx_id, direction="in", resolved_person_id=someone.id, content=OWN, trust_at_time=0,
            audience_person_ids=[], audience_has_unknown=True,
        )  # fmt: skip
        s.add(ev)
        s.flush()
        from superaide.journal import service

        service.schedule(s, ev)  # Priya is not a journal owner: nothing queued
        assert s.scalar(select(Job).where(Job.kind == "journal_from_turn")) is None
