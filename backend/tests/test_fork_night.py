"""Fork Night (#127): three read-only drafts, queued for the night, picked in the morning. Off by default."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from superaide.brain.budget import BudgetExceeded, bucket_for
from superaide.brain.llm import FakeLLM
from superaide.db.models import Job, Person
from superaide.db.session import session_scope
from superaide.forknight import night

SECRET = "Atlas acquisition codename Bluebird"


class Agent:
    def __init__(self, settings, responder) -> None:
        self.settings = settings
        self.llm = FakeLLM(responder)


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "fork_night_enabled", True)


def drafts(purpose, system, messages, output_model):
    assert purpose == "fork_night" and output_model is night.Branch
    lens = next(k for k, v in night.LENSES.items() if v in "\n".join(b.text for b in system))
    return night.Branch(plan=f"{lens} plan for the trip", unknowns=[f"{lens}: visa rules?"], confidence=0.6)


def owner() -> Person:
    with session_scope() as s:
        o = Person(display_name="Shashank", relation="manager")
        s.add(o)
        s.flush()
        s.expunge(o)
        return o


def queued(settings, question="Plan a 3 day trip to Lisbon in March") -> str:
    o = owner()
    with session_scope() as s:
        return night.queue(s, s.get(Person, o.id), settings, question, now=datetime(2026, 10, 23, 15, 0, tzinfo=UTC))[
            "id"
        ]


def test_off_by_default(settings):
    assert not night.enabled(settings)


def test_queue_books_a_job_for_the_small_hours(on, settings):
    rid = queued(settings)
    with session_scope() as s:
        job = s.scalar(select(Job).where(Job.kind == "fork_night"))
        assert job.payload == {"run_id": rid} and job.run_at.hour == 2  # 02:00 UTC (the owner's zone defaults to UTC)
        assert job.run_at > datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
        assert night.listing(s)[0]["status"] == "queued"


def test_three_branches_each_with_unknowns_and_confidence(on, settings):
    rid = queued(settings)
    agent = Agent(settings, drafts)
    with session_scope() as s:
        item = night.run(agent, s, rid)
    assert [b["lens"] for b in item["branches"]] == ["careful", "quick", "frugal"] and item["status"] == "ready"
    assert all(b["unknowns"] and 0 <= b["confidence"] <= 1 for b in item["branches"])


def test_branches_see_only_the_question_and_complete_has_no_tools_argument(on, settings):
    from superaide.knowledge.store import remember_fact

    with session_scope() as s:
        o = Person(display_name="Shashank", relation="manager")
        s.add(o)
        s.flush()
        remember_fact(s, owner=o, content=SECRET, category="work")
        oid = o.id
    with session_scope() as s:
        rid = night.queue(s, s.get(Person, oid), settings, "Plan a trip")["id"]
    agent = Agent(settings, drafts)
    with session_scope() as s:
        night.run(agent, s, rid)
    import inspect

    from superaide.brain.llm import FakeLLM as F

    assert "tools" not in inspect.signature(F.complete).parameters  # a branch cannot be handed a tool
    assert len(agent.llm.calls) == 3
    for call in agent.llm.calls:
        blob = "\n".join(b.text for b in call["system"]) + str(call["messages"])
        assert "Bluebird" not in blob and "Shashank" not in blob  # no memory, no owner brief
        assert call["messages"][0]["content"].count("Plan a trip") == 1


def test_the_budget_stops_later_branches_and_nothing_is_bought_elsewhere(on, settings):
    calls = []

    def run_out(purpose, system, messages, output_model):
        calls.append(1)
        if len(calls) == 2:
            raise BudgetExceeded("daily fork budget spent")
        return drafts(purpose, system, messages, output_model)

    rid = queued(settings)
    with session_scope() as s:
        item = night.run(Agent(settings, run_out), s, rid)
    assert [b["lens"] for b in item["branches"]] == ["careful"] and len(calls) == 2


def test_a_run_with_no_branches_is_failed_and_does_not_rerun(on, settings):
    def refuse(*a):
        raise BudgetExceeded("spent")

    rid = queued(settings)
    with session_scope() as s:
        assert night.run(Agent(settings, refuse), s, rid)["status"] == "failed"
    with session_scope() as s:
        assert night.run(Agent(settings, drafts), s, rid) is None  # only a queued run runs


def test_pick_records_one_of_the_three(on, settings):
    rid = queued(settings)
    with session_scope() as s:
        night.run(Agent(settings, drafts), s, rid)
    with session_scope() as s:
        assert night.pick(s, rid, "frugal") and not night.pick(s, rid, "reckless")
        assert night.listing(s)[0]["picked"] == "frugal"


def test_runs_draw_from_their_own_daily_bucket(settings):
    assert bucket_for("fork_night") == "fork" and settings.budget_daily_by_bucket["fork"] > 0
