"""POST /app/dissent (#126): 404 for the owner while off, 401/403 for everyone else, a verdict when on."""

from __future__ import annotations

from superaide.brain.llm import FakeLLM
from superaide.dissent import desk
from tests.api.conftest import OTHER, signup
from tests.test_dissent import CAUTION, MAJORITY, OPTIONS, scripted

BODY = {"question": "Reply angrily to the client?", "options": OPTIONS, "kind": "gmail_send"}


def test_off_by_default_the_owner_gets_404(owner):
    assert owner.post("/app/dissent", json=BODY).status_code == 404


def test_others_are_refused_first(browser, owner):
    assert browser().post("/app/dissent", json=BODY).status_code in (401, 403)
    stranger = browser()
    signup(stranger, **OTHER)
    assert stranger.post("/app/dissent", json=BODY).status_code == 403


def test_on_it_returns_the_minority_view(owner, agent, settings, monkeypatch):
    monkeypatch.setattr(settings, "dissent_desk_enabled", True)
    monkeypatch.setattr(settings, "dissent_models", "m1, m2, m3")
    agent.llm = FakeLLM(scripted((0, 0.8, MAJORITY), (0, 0.8, MAJORITY), (1, 0.9, CAUTION)))
    r = owner.post("/app/dissent", json=BODY)
    assert r.status_code == 200 and r.json()["ran"] is True and r.json()["dissent"]["choice"] == 1
    assert "One adviser disagrees" in r.json()["lines"][1]
    assert desk.stakes_of("gmail_send") >= settings.dissent_min_stakes


def test_low_stakes_do_not_run(owner, agent, settings, monkeypatch):
    monkeypatch.setattr(settings, "dissent_desk_enabled", True)
    monkeypatch.setattr(settings, "dissent_models", "m1, m2, m3")
    assert owner.post("/app/dissent", json={**BODY, "kind": ""}).json()["ran"] is False
