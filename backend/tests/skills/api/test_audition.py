"""Audition (#110, use 2): candidate skills side by side on the last tasks, instructions only, no network."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from superaide.platform.db.models import Intention, ToolTrace
from superaide.platform.db.session import session_scope
from superaide.tooltrace.recorder import digest
from tests.rehearsal.rehearsal_support import live_history, results_seen, switch

GMAIL, CAL, WEB = "superaide/gmail", "superaide/google-calendar", "superaide/web-research"


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "audition_enabled", True)


def by_skill(model, system, messages):
    """Each candidate's instructions make Ava reach for that skill's own tool first."""
    n = results_seen(messages)
    if n == 0:
        for marker, tool in (("gmail_search", "gmail_search"), ("calendar_events", "calendar_events")):
            if marker in system:
                return [{"type": "tool_use", "id": "a", "name": tool, "input": {"query": "important"}}]
        return [{"type": "tool_use", "id": "a", "name": "recall", "input": {"query": "important"}}]
    if n == 1 and "gmail_search" in system:
        return [{"type": "tool_use", "id": "b", "name": "gmail_send", "input": {"to": "x@example.com", "text": "hi"}}]
    return "Done."


def history(n=3):
    with session_scope() as s:
        live_history(s, n)


def test_off_by_default(owner):
    assert owner.get("/app/audition/candidates").status_code == 404
    assert owner.post("/app/audition", json={"skills": [GMAIL, CAL]}).status_code == 404


def test_the_candidates_are_the_catalogue(owner, on):
    ids = {c["id"] for c in owner.get("/app/audition/candidates").json()}
    assert {GMAIL, CAL, WEB} <= ids


def test_two_candidates_are_shown_side_by_side_on_each_task(owner, on, agent):
    switch(agent, by_skill)
    history(3)
    r = owner.post("/app/audition", json={"skills": [GMAIL, CAL]})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["tasks_done"] == 3 and a["agree_on"] == 0 and a["differ"] == [{"a": GMAIL, "b": CAL, "tasks": 3}]
    t = a["tasks"][0]
    assert [c["tool"] for c in t["candidates"][GMAIL]] == ["gmail_search", "gmail_send"]
    assert [c["tool"] for c in t["candidates"][CAL]] == ["calendar_events"]
    assert a["totals"][GMAIL]["would_change_things"] == 3  # one send per task, all stubbed


def test_three_candidates_and_the_limits(owner, on, agent):
    switch(agent, by_skill)
    history(2)
    a = owner.post("/app/audition", json={"skills": [GMAIL, CAL, WEB]}).json()
    assert len(a["differ"]) == 3 and set(a["tasks"][0]["candidates"]) == {GMAIL, CAL, WEB}
    assert owner.post("/app/audition", json={"skills": [GMAIL]}).status_code == 422
    assert owner.post("/app/audition", json={"skills": [GMAIL, CAL, WEB, "x"]}).status_code == 422
    assert owner.post("/app/audition", json={"skills": [GMAIL, GMAIL]}).status_code == 422  # one skill twice is not two
    assert owner.post("/app/audition", json={"skills": [GMAIL, "superaide/nope"]}).status_code == 404


def test_nothing_is_sent_or_changed_and_no_content_is_shown(owner, on, agent):
    switch(agent, by_skill)
    history(2)
    with session_scope() as s:
        before = (
            s.scalar(select(func.count()).select_from(Intention)),
            s.scalar(select(func.count()).select_from(ToolTrace)),
        )
    r = owner.post("/app/audition", json={"skills": [GMAIL, CAL]})
    for text in ("Please sort out", "x@example.com", "important", "Call person"):
        assert text not in r.text
    assert digest({"to": "x@example.com", "text": "hi"})[:12] in r.text  # only a short hash of the send
    with session_scope() as s:
        assert (
            s.scalar(select(func.count()).select_from(Intention)),
            s.scalar(select(func.count()).select_from(ToolTrace)),
        ) == before


def test_no_network_tool_runs_for_real_even_a_read(owner, on, agent, monkeypatch):
    """Gmail and calendar reads would call Google; the executor answers them with a canned result instead."""
    import superaide.skills.gmail as gmail

    called = []
    monkeypatch.setattr(gmail, "search", lambda *a, **k: called.append("gmail") or "REAL", raising=False)
    switch(agent, by_skill)
    history(2)
    owner.post("/app/audition", json={"skills": [GMAIL, CAL]})
    assert called == []
    assert any(c["system"] for c in agent.llm.calls)
    seen = [m for c in agent.llm.calls for m in c["messages"] if isinstance(m.get("content"), list)]
    assert any("rehearsal" in str(m["content"]) for m in seen)  # the model was handed the canned answer


def test_the_number_of_tasks_is_capped(owner, on, agent, monkeypatch, settings):
    monkeypatch.setattr(settings, "audition_max_tasks", 2)
    switch(agent, by_skill)
    history(4)
    assert owner.post("/app/audition", json={"skills": [GMAIL, CAL], "tasks": 50}).json()["tasks_asked"] == 2


def test_an_empty_history_says_so(owner, on, agent):
    switch(agent, by_skill)
    assert owner.post("/app/audition", json={"skills": [GMAIL, CAL]}).status_code == 409


def test_another_owner_is_refused(other, on):
    assert other.post("/app/audition", json={"skills": [GMAIL, CAL]}).status_code == 403
    assert other.get("/app/audition/candidates").status_code == 403
