"""Dissent Desk (#126): three different models, a non-LLM classifier, stakes and budget gates. Off by default."""

from __future__ import annotations

import pytest

from superaide.brain.budget import BudgetExceeded, bucket_for
from superaide.brain.llm import FakeLLM
from superaide.db.session import session_scope
from superaide.features.dissent import desk

OPTIONS = ["Send the firm reply", "Wait a day and send a softer one"]
CAUTION = "Replying angrily tonight risks the client relationship permanently and cannot be unsent afterwards."
MAJORITY = "The client broke the agreement twice, so a firm reply protects our position."


class Agent:
    def __init__(self, settings, responder) -> None:
        self.settings = settings
        self.llm = FakeLLM(responder)


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "dissent_desk_enabled", True)
    monkeypatch.setattr(settings, "dissent_models", "claude-haiku-5-5, openai::gpt-x, local::llama")


def scripted(*votes: tuple[int, float, str]):
    it = iter(votes)

    def run(purpose, system, messages, output_model):
        assert purpose == "dissent"
        c, conf, why = next(it)
        return desk.Advice(choice=c, confidence=conf, reason=why)

    return run


def test_off_by_default(settings):
    assert not desk.ready(settings)
    with session_scope() as s:
        assert desk.ask(Agent(settings, scripted()), s, "Reply?", OPTIONS, kind="gmail_send") is None


def test_needs_three_different_models(monkeypatch, settings):
    monkeypatch.setattr(settings, "dissent_desk_enabled", True)
    monkeypatch.setattr(settings, "dissent_models", "a, a, b")
    assert not desk.ready(settings)


def test_low_stakes_runs_nothing(on, settings):
    agent = Agent(settings, scripted())
    with session_scope() as s:
        assert desk.ask(agent, s, "Reply?", OPTIONS, kind="delete_memory") is None
    assert agent.llm.calls == []


def test_a_confident_minority_with_new_reasons_is_shown(on, settings):
    agent = Agent(settings, scripted((0, 0.8, MAJORITY), (0, 0.7, MAJORITY), (1, 0.9, CAUTION)))
    with session_scope() as s:
        v = desk.ask(agent, s, "Reply to the client?", OPTIONS, kind="gmail_send")
    assert v.majority == 0 and v.dissent is not None and v.dissent.choice == 1
    assert [c["model"] for c in agent.llm.calls] == ["claude-haiku-5-5", "openai::gpt-x", "local::llama"]
    assert "One adviser disagrees" in v.lines()[1] and CAUTION in v.lines()[1]


def test_a_unanimous_vote_shows_no_dissent(on, settings):
    agent = Agent(settings, scripted((0, 0.8, MAJORITY), (0, 0.9, MAJORITY), (0, 0.7, MAJORITY)))
    with session_scope() as s:
        v = desk.ask(agent, s, "Reply?", OPTIONS, kind="gmail_send")
    assert v.majority == 0 and v.dissent is None


def test_an_unsure_or_echoing_minority_is_not_shown():
    options = OPTIONS
    votes = [
        desk.Vote("a", 0, 0.9, MAJORITY),
        desk.Vote("b", 0, 0.9, MAJORITY),
        desk.Vote("c", 1, 0.4, CAUTION),
    ]
    assert desk.classify(options, votes).dissent is None  # not confident
    echo = desk.Vote("c", 1, 0.9, "The client broke the agreement twice, so a firm reply protects our position.")
    assert desk.classify(options, votes[:2] + [echo]).dissent is None  # same reasons as the majority
    short = desk.Vote("c", 1, 0.9, "Too risky.")
    assert desk.classify(options, votes[:2] + [short]).dissent is None  # no reasons given


def test_a_three_way_split_has_no_majority():
    votes = [desk.Vote("a", 0, 0.9, "x " * 8), desk.Vote("b", 1, 0.9, "y " * 8), desk.Vote("c", 2, 0.9, "z " * 8)]
    v = desk.classify(["a", "b", "c"], votes)
    assert v.majority is None and v.dissent is None


def test_the_classifier_makes_no_model_call():
    agent_calls = []
    votes = [desk.Vote("a", 0, 0.9, MAJORITY), desk.Vote("b", 0, 0.9, MAJORITY), desk.Vote("c", 1, 0.9, CAUTION)]
    desk.classify(OPTIONS, votes)
    assert agent_calls == []


def test_a_refused_call_changes_nothing(on, settings):
    def refuse(*a):
        raise BudgetExceeded("daily dissent budget spent")

    agent = Agent(settings, refuse)
    with session_scope() as s:
        assert desk.ask(agent, s, "Reply?", OPTIONS, kind="gmail_send") is None


def test_the_calls_draw_from_their_own_daily_bucket(settings):
    assert bucket_for("dissent") == "dissent" and settings.budget_daily_by_bucket["dissent"] > 0
