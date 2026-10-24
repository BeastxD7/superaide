"""/app/standing-orders (#52): off for the owner until enabled, 401/403 for others, the whole life of a rule."""

from __future__ import annotations

from tests.api_support import OTHER, signup

SPEC = {"keywords": ["recruiter"], "action": "label", "value": "recruiter"}


def test_off_by_default_the_owner_gets_404(owner):
    assert owner.get("/app/standing-orders").status_code == 404
    assert owner.post("/app/standing-orders", json={"text": "label recruiters", "spec": SPEC}).status_code == 404


def test_others_are_refused_first(browser, owner):
    assert browser().get("/app/standing-orders").status_code in (401, 403)
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.get("/app/standing-orders").status_code == 403


def test_create_replay_activate_and_the_refusals(owner, settings, monkeypatch):
    monkeypatch.setattr(settings, "time_travel_rules_enabled", True)
    r = owner.post("/app/standing-orders", json={"text": "label recruiters", "spec": SPEC})
    assert r.status_code == 200 and "mentions “recruiter”" in r.json()["plain"] and r.json()["status"] == "draft"
    rid = r.json()["id"]
    assert owner.post(f"/app/standing-orders/{rid}/activate").status_code == 409  # no replay yet
    rep = owner.post(f"/app/standing-orders/{rid}/replay")
    assert rep.status_code == 200 and rep.json()["fired"] == 0 and "Only you can see this" in rep.json()["note"]
    assert owner.post(f"/app/standing-orders/{rid}/activate").json() == {"status": "shadow"}
    assert owner.post(f"/app/standing-orders/{rid}/promote").status_code == 409  # not enough agreeing firings
    shown = owner.get("/app/standing-orders").json()["rules"]
    assert shown[0]["status"] == "shadow" and shown[0]["replay"]["fp"] == shown[0]["fp"]
    assert owner.put(f"/app/standing-orders/{rid}", json={**SPEC, "keywords": ["invoice"]}).status_code == 200
    assert owner.get("/app/standing-orders").json()["rules"][0]["status"] == "draft"  # editing voided the replay
    assert owner.delete(f"/app/standing-orders/{rid}").status_code == 200
    assert owner.delete(f"/app/standing-orders/{rid}").status_code == 404


def test_a_rule_the_compiler_cannot_make_is_a_422_not_a_guess(owner, settings, monkeypatch):
    monkeypatch.setattr(settings, "time_travel_rules_enabled", True)
    # the offline model returns no structured rule
    assert owner.post("/app/standing-orders", json={"text": "do something clever"}).status_code == 422
