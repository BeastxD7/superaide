"""Model-swap drill (#110, use 3): the same tasks on two models, and where their call sequences differ."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from superaide.intentions.models import Intention
from superaide.platform.db.session import session_scope
from superaide.tooltrace.models import ToolTrace
from tests.rehearsal.rehearsal_support import live_history, results_seen, switch

OLD, NEW = "claude-haiku-5-5", "claude-sonnet-5-5"


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "model_drill_enabled", True)


def by_model(model, system, messages):
    """The bigger model recalls first and then sets a reminder; the small one goes straight to the reminder."""
    n = results_seen(messages)
    if "sonnet" in model:
        steps = [("recall", {"query": "flight"}), ("create_reminder", {"task": "Follow up", "when": "friday"})]
    else:
        steps = [("create_reminder", {"task": "Follow up", "when": "friday"})]
    if n < len(steps):
        name, args = steps[n]
        return [{"type": "tool_use", "id": f"t{n}", "name": name, "input": args}]
    return "Done."


def history(n=3):
    with session_scope() as s:
        live_history(s, n)


def test_off_by_default(owner):
    assert owner.get("/app/model-drill").status_code == 404
    assert owner.post("/app/model-drill", json={"model_b": NEW}).status_code == 404


def test_info_says_what_can_be_replayed(owner, on, agent):
    history(2)
    info = owner.get("/app/model-drill").json()
    assert info["current_model"] == OLD and info["tasks_available"] == 2 and info["max_tasks"] == 20


def test_the_drill_reports_where_the_two_models_part_ways(owner, on, agent):
    switch(agent, by_model)
    history(3)
    r = owner.post("/app/model-drill", json={"model_b": NEW})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["model_a"] == OLD and d["model_b"] == NEW  # model_a defaults to the model in use
    assert d["headline"] == "3 of 3 would change." and d["changed"] == 3 and d["tasks_done"] == 3
    t = d["tasks"][0]
    assert (t["calls_a"], t["calls_b"], t["first_difference_at"]) == (1, 2, 1)
    assert d["only_model_b"] == {"recall": 3} and d["only_model_a"] == {}


def test_the_same_behaviour_on_both_models_is_no_change(owner, on, agent):
    switch(agent, lambda m, s, msgs: by_model(NEW, s, msgs))
    history(2)
    d = owner.post("/app/model-drill", json={"model_a": OLD, "model_b": NEW}).json()
    assert d["headline"] == "0 of 2 would change." and d["tasks"][0]["first_difference_at"] is None


def test_nothing_is_sent_or_changed_and_no_content_is_shown(owner, on, agent):
    switch(agent, by_model)
    history(2)
    with session_scope() as s:
        before = (
            s.scalar(select(func.count()).select_from(Intention)),
            s.scalar(select(func.count()).select_from(ToolTrace)),
        )
    r = owner.post("/app/model-drill", json={"model_b": NEW})
    for text in ("Please sort out", "Follow up", "friday", "Call person"):
        assert text not in r.text
    with session_scope() as s:
        assert (
            s.scalar(select(func.count()).select_from(Intention)),
            s.scalar(select(func.count()).select_from(ToolTrace)),
        ) == before
    assert {c["model"] for c in agent.llm.calls} == {OLD, NEW}
    assert {c["purpose"] for c in agent.llm.calls} == {"replay_turn"}


def test_bad_requests(owner, on, agent):
    switch(agent, by_model)
    assert owner.post("/app/model-drill", json={"model_b": NEW}).status_code == 409  # nothing to replay yet
    history(1)
    assert owner.post("/app/model-drill", json={"model_b": OLD}).status_code == 422  # the same model twice
    assert owner.post("/app/model-drill", json={"model_b": ""}).status_code == 422
    assert owner.post("/app/model-drill", json={"model_b": "claude-not-priced-9"}).status_code == 422
    assert owner.post("/app/model-drill", json={"model_b": NEW, "tasks": 99}).status_code == 422


def test_the_number_of_tasks_is_capped(owner, on, agent, monkeypatch, settings):
    monkeypatch.setattr(settings, "drill_max_tasks", 2)
    switch(agent, by_model)
    history(4)
    assert owner.post("/app/model-drill", json={"model_b": NEW, "tasks": 50}).json()["tasks_asked"] == 2


def test_a_refused_call_returns_what_was_done(owner, on, agent):
    from superaide.brain.spend.budget import BudgetExceeded

    calls = {"n": 0}

    def flaky(model, system, messages):
        calls["n"] += 1
        if calls["n"] > 5:
            raise BudgetExceeded("daily limit")
        return by_model(model, system, messages)

    switch(agent, flaky)
    history(4)
    d = owner.post("/app/model-drill", json={"model_b": NEW}).json()
    assert d["stopped"].startswith("spend limit") and 0 < d["tasks_done"] < 4 and d["tasks_asked"] == 4


def test_another_owner_is_refused(other, on):
    assert other.get("/app/model-drill").status_code == 403
    assert other.post("/app/model-drill", json={"model_b": NEW}).status_code == 403
