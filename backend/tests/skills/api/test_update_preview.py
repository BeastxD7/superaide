"""Replay before update (#110, use 1): see what a change would do differently, then approve or reject."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from superaide.db.models import InstalledSkill, Intention, ToolTrace
from superaide.db.session import session_scope
from superaide.skills import registry
from tests.rehearsal.rehearsal_support import live_history, results_seen, switch


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "replay_before_update_enabled", True)


def candidate_behaviour(model, system, messages):
    """The candidate prompt or skill adds a follow-up reminder; a different model skips the recall."""
    n = results_seen(messages)
    changed = "FOLLOWUP" in system or "claude-opus" in model
    if n == 0 and "claude-opus" not in model:
        return [{"type": "tool_use", "id": "a", "name": "recall", "input": {"query": "flight"}}]
    if n <= 1 and changed:
        return [
            {"type": "tool_use", "id": "b", "name": "create_reminder", "input": {"task": "Follow up", "when": "friday"}}
        ]
    return "Done."


def history(n=3):
    with session_scope() as s:
        live_history(s, n)


def test_off_by_default(owner):
    assert owner.get("/app/updates").status_code == 404
    assert owner.post("/app/updates/preview", json={"kind": "model", "model": "x"}).status_code == 404


def test_a_prompt_change_shows_how_many_tasks_would_go_differently(owner, on, agent):
    switch(agent, candidate_behaviour)
    history(3)
    r = owner.post("/app/updates/preview", json={"kind": "prompt", "prompt": "FOLLOWUP: also set a follow-up."})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["status"] == "pending" and p["tasks_done"] == 3 and p["changed"] == 3
    assert p["headline"] == "3 of your last 3 tasks would go differently."
    first = p["tasks"][0]
    assert first["only_after"][0]["tool"] == "create_reminder" and first["only_before"] == []
    assert set(first["only_after"][0]) == {"tool", "effect", "args"}  # names and a short hash, nothing else
    assert owner.get("/app/updates").json()[0]["id"] == p["id"]


def test_a_preview_changes_nothing_and_shows_no_content(owner, on, agent):
    switch(agent, candidate_behaviour)
    history(2)
    with session_scope() as s:
        before = (
            s.scalar(select(func.count()).select_from(Intention)),
            s.scalar(select(func.count()).select_from(ToolTrace)),
        )
    r = owner.post("/app/updates/preview", json={"kind": "prompt", "prompt": "FOLLOWUP: secret-instructions"})
    for text in ("Please sort out", "Follow up", "Call person", "secret-instructions", "friday"):
        assert text not in r.text
    with session_scope() as s:
        assert (
            s.scalar(select(func.count()).select_from(Intention)),
            s.scalar(select(func.count()).select_from(ToolTrace)),
        ) == before
        stored = str(s.scalar(select(__import__("superaide.tooltrace.models", fromlist=["x"]).UpdatePreview.detail)))
        assert "secret-instructions" not in stored and "sha256" in stored  # only a hash of the prompt is kept


def test_an_identical_setup_changes_nothing(owner, on, agent):
    switch(agent, candidate_behaviour)
    history(2)
    p = owner.post("/app/updates/preview", json={"kind": "model", "model": "claude-haiku-5-5"}).json()
    assert p["changed"] == 0 and p["headline"].startswith("No difference")


def test_a_model_change_is_compared_too(owner, on, agent):
    switch(agent, candidate_behaviour)
    history(2)
    p = owner.post("/app/updates/preview", json={"kind": "model", "model": "claude-opus-5"}).json()
    assert p["changed"] == 2 and p["tasks"][0]["only_before"][0]["tool"] == "recall"


def test_approve_a_skill_installs_it_and_reject_does_not(owner, on, agent):
    switch(agent, candidate_behaviour)
    history(2)
    with session_scope() as s:
        registry.ensure_defaults(s)
        candidates = [i for i, k in registry.catalog().items() if not k.manifest.default_installed]
    if not candidates:
        pytest.skip("every built-in skill is installed by default")
    skill_id = candidates[0]
    needed = registry.catalog()[skill_id].manifest.permissions
    p = owner.post("/app/updates/preview", json={"kind": "skill", "skill_id": skill_id}).json()
    assert owner.post(f"/app/updates/{p['id']}/approve", json={"accepted": []}).status_code in (409, 200)
    q = owner.post("/app/updates/preview", json={"kind": "skill", "skill_id": skill_id}).json()
    ok = owner.post(f"/app/updates/{q['id']}/approve", json={"accepted": needed})
    if ok.status_code == 200:
        assert ok.json()["applied"] is True and ok.json()["status"] == "approved"
        with session_scope() as s:
            assert s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == skill_id)) is not None
        assert owner.post(f"/app/updates/{q['id']}/approve", json={"accepted": needed}).status_code == 409
    r = owner.post("/app/updates/preview", json={"kind": "skill", "skill_id": skill_id}).json()
    assert owner.post(f"/app/updates/{r['id']}/reject").json()["status"] == "rejected"
    assert owner.post(f"/app/updates/{r['id']}/approve", json={"accepted": needed}).status_code == 409


def test_approving_a_prompt_is_recorded_not_applied(owner, on, agent):
    switch(agent, candidate_behaviour)
    history(1)
    p = owner.post("/app/updates/preview", json={"kind": "prompt", "prompt": "FOLLOWUP"}).json()
    r = owner.post(f"/app/updates/{p['id']}/approve", json={}).json()
    assert r["status"] == "approved" and r["applied"] is False


def test_bad_requests(owner, on, agent):
    switch(agent, candidate_behaviour)
    assert owner.post("/app/updates/preview", json={"kind": "model", "model": "x"}).status_code == 409  # no history yet
    history(1)
    assert owner.post("/app/updates/preview", json={"kind": "nope"}).status_code == 422
    assert owner.post("/app/updates/preview", json={"kind": "skill", "skill_id": "no-such-skill"}).status_code == 404
    assert owner.post("/app/updates/preview", json={"kind": "model", "model": " "}).status_code == 422
    assert owner.post("/app/updates/preview", json={"kind": "prompt", "prompt": ""}).status_code == 422
    assert owner.post("/app/updates/preview", json={"kind": "model", "model": "m", "tasks": 99}).status_code == 422
    assert owner.post(f"/app/updates/{uuid.uuid4()}/reject").status_code == 404


def test_the_number_of_tasks_is_capped(owner, on, agent, monkeypatch, settings):
    monkeypatch.setattr(settings, "replay_max_tasks", 2)
    switch(agent, candidate_behaviour)
    history(4)
    assert (
        owner.post("/app/updates/preview", json={"kind": "prompt", "prompt": "x", "tasks": 50}).json()["tasks_asked"]
        == 2
    )


def test_another_owner_cannot_see_or_decide_a_preview(owner, other, on, agent):
    switch(agent, candidate_behaviour)
    history(1)
    p = owner.post("/app/updates/preview", json={"kind": "prompt", "prompt": "x"}).json()
    assert other.post(f"/app/updates/{p['id']}/reject").status_code == 403
    assert other.get("/app/updates").status_code == 403
