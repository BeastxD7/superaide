"""/app/fork-night (#127): 404 for the owner while off, 401/403 for others, queue and pick when on."""

from __future__ import annotations

from superaide.brain.llm import FakeLLM
from superaide.features.forknight import night
from superaide.platform.db.session import session_scope
from tests.api_support import OTHER, signup
from tests.forknight.test_fork_night import drafts


def test_off_by_default_the_owner_gets_404(owner):
    assert owner.get("/app/fork-night").status_code == 404
    assert owner.post("/app/fork-night", json={"question": "Plan a trip"}).status_code == 404


def test_others_are_refused_first(browser, owner):
    assert browser().get("/app/fork-night").status_code in (401, 403)
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.get("/app/fork-night").status_code == 403


def test_queue_read_and_pick(owner, agent, settings, monkeypatch):
    monkeypatch.setattr(settings, "fork_night_enabled", True)
    r = owner.post("/app/fork-night", json={"question": "Plan a 3 day trip to Lisbon"})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    rid = r.json()["id"]
    agent.llm = FakeLLM(drafts)
    with session_scope() as s:
        night.run(agent, s, rid)
    runs = owner.get("/app/fork-night").json()["runs"]
    assert runs[0]["status"] == "ready" and len(runs[0]["branches"]) == 3
    assert owner.post(f"/app/fork-night/{rid}/pick", json={"lens": "careful"}).status_code == 200
    assert owner.post(f"/app/fork-night/{rid}/pick", json={"lens": "nope"}).status_code == 404
    assert owner.get("/app/fork-night").json()["runs"][0]["picked"] == "careful"
