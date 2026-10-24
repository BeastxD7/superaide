"""Memory-free second opinion (#123): an empty-memory call, a measured agreement rate, a forced other side."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from superaide.brain.budget import BudgetExceeded
from superaide.brain.llm import FakeLLM
from superaide.knowledge.store import remember_fact
from superaide.platform.db.models import Person
from superaide.platform.db.session import session_scope
from superaide.proactive.digest import compose
from superaide.trust import second_opinion as so
from superaide.trust.models import AgreementLog

SECRET = "The owner believes the Atlas migration needs no testing."


class Agent:
    """The two things `review` needs from an agent."""

    def __init__(self, settings, responder) -> None:
        self.settings = settings
        self.llm = FakeLLM(responder)


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "second_opinion_enabled", True)
    monkeypatch.setattr(settings, "agreement_limit", 0.75)


def responder(independent: str = "disagree", verdict: str = "agree", judges: bool = True):
    def run(purpose, system, messages, output_model):
        assert purpose == "second_opinion"
        text = "\n".join(b.text for b in system)
        stance = verdict if "Does the assistant's reply" in text else independent
        return so.Stance(judges_owners_own_view=judges, stance=stance, strongest_other_side="Untested migrations fail.")

    return run


def owner_with_memory():
    with session_scope() as s:
        o = Person(display_name="Jordan Whitfield", relation="manager")
        s.add(o)
        s.flush()
        remember_fact(s, owner=o, content=SECRET, category="work")
        return o.id


def test_the_memory_free_call_gets_nothing_but_the_message():
    owner_with_memory()
    system, messages = so.second_opinion_inputs("I think we should skip testing.")
    blob = "\n".join(b.text for b in system) + str(messages)
    assert "Atlas" not in blob and "Jordan" not in blob and "Memories" not in blob
    assert len(messages) == 1 and "skip testing" in messages[0]["content"]


def test_a_disagreeing_second_reader_is_shown_when_the_reply_flattered(on, settings):
    oid = owner_with_memory()
    agent = Agent(settings, responder(independent="disagree", verdict="agree"))
    with session_scope() as s:
        out = so.review(agent, s, s.get(Person, oid), "Skip testing the migration?", "Good plan, skip it.")
        assert "second reader" in out and "Untested migrations fail." in out
        assert s.scalar(select(AgreementLog.flagged)) is True
    # Nothing the memory-free call received mentions memory.
    assert all("Atlas" not in str(c["system"]) + str(c["messages"]) for c in agent.llm.calls)


def test_a_reply_that_pushed_back_is_left_alone(on, settings):
    oid = owner_with_memory()
    agent = Agent(settings, responder(independent="disagree", verdict="disagree"))
    with session_scope() as s:
        assert so.review(agent, s, s.get(Person, oid), "Skip testing?", "I would test it.") == "I would test it."
        assert s.scalar(select(AgreementLog.validated)) is False


def test_a_message_that_is_not_about_the_owners_own_view_costs_one_call_and_logs_nothing(on, settings):
    oid = owner_with_memory()
    agent = Agent(settings, responder(judges=False))
    with session_scope() as s:
        assert so.review(agent, s, s.get(Person, oid), "What is 2+2?", "4") == "4"
        assert s.scalar(select(AgreementLog.id)) is None
    assert len(agent.llm.calls) == 1


def test_above_the_limit_the_other_side_is_forced_even_when_the_second_reader_agrees(on, settings):
    oid = owner_with_memory()
    agent = Agent(settings, responder(independent="agree", verdict="agree"))
    with session_scope() as s:
        me = s.get(Person, oid)
        outs = [so.review(agent, s, me, f"My plan {i}?", "Yes, great.") for i in range(so.MIN_LOGGED)]
        assert all("strongest case" not in o for o in outs[:-1])  # not enough history yet
        assert "The strongest case for the other side" in outs[-1]  # 100% validated > the 75% limit
        rate, n = so.validation_rate(s, me)
        assert rate == 1.0 and n == so.MIN_LOGGED


def test_a_balanced_record_is_not_nagged(on, settings):
    oid = owner_with_memory()
    with session_scope() as s:
        me = s.get(Person, oid)
        for i in range(so.MIN_LOGGED):
            s.add(AgreementLog(person_id=me.id, validated=i % 2 == 0, second_stance="agree", flagged=False))
        s.flush()
        agent = Agent(settings, responder(independent="agree", verdict="disagree"))
        assert so.review(agent, s, me, "My plan?", "Hmm, no.") == "Hmm, no."


def test_a_refused_or_failing_call_never_loses_the_reply(on, settings):
    oid = owner_with_memory()

    def refused(*a):
        raise BudgetExceeded("daily limit")

    def broken(*a):
        raise RuntimeError("boom")

    with session_scope() as s:
        me = s.get(Person, oid)
        assert so.review(Agent(settings, refused), s, me, "My plan?", "Yes.") == "Yes."
        assert so.review(Agent(settings, broken), s, me, "My plan?", "Yes.") == "Yes."


def test_the_weekly_digest_carries_the_agreement_rate_on_mondays(on, settings):
    oid = owner_with_memory()
    with session_scope() as s:
        me = s.get(Person, oid)
        for _ in range(so.MIN_LOGGED):
            s.add(AgreementLog(person_id=me.id, validated=True, second_stance="agree", flagged=False))
        s.flush()
        monday = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)  # a Monday
        text = compose(s, me, monday, "morning", events=[])
        assert text and "My agreement rate" in text and "100%" in text
        tuesday = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)
        assert "agreement rate" not in (compose(s, me, tuesday, "morning", events=[]) or "")
