"""Ava halted from chat (halt word, breaker, witness) can be resumed from the app and from the console (#281)."""

from __future__ import annotations

import pytest

from superaide.platform.config import get_settings
from tests.api_support import hire, signup

ADMIN = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def halted(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "circuit_breaker_enabled", True)
    c = browser()
    signup(c), hire(c)
    reply = c.post("/app/chat", json={"text": get_settings().halt_word}).json()
    assert "paused" in str(reply).lower()
    me = c.get("/app/me").json()
    assert me["paused"] is True
    return c


def test_the_owner_can_resume_what_the_halt_word_stopped(halted):
    assert halted.get("/app/me").json()["can_resume"] is True
    r = halted.post("/app/resume")
    assert r.status_code == 200 and r.json() == {"paused": False}
    me = halted.get("/app/me").json()
    assert me["paused"] is False and me["can_resume"] is False


def test_the_console_resume_lifts_the_halt_too(halted):
    assert halted.get("/app/me").json()["paused"] is True
    r = halted.post("/api/kill", json={"on": False}, headers=ADMIN)
    assert r.status_code == 200 and r.json() == {"frozen": False}
    assert halted.get("/app/me").json()["paused"] is False


def test_the_console_overview_shows_an_instance_halt_as_paused(halted):
    assert halted.get("/api/overview", headers=ADMIN).json()["frozen"] is True


def test_an_operator_pause_is_not_the_owners_to_lift(browser):
    c = browser()
    signup(c), hire(c)
    assert c.post("/api/kill", json={"on": True}, headers=ADMIN).status_code == 200
    me = c.get("/app/me").json()
    assert me["paused"] is True and me["can_resume"] is False
    r = c.post("/app/resume")
    assert r.status_code == 409
    assert c.get("/app/me").json()["paused"] is True
    assert c.post("/api/kill", json={"on": False}, headers=ADMIN).status_code == 200
    assert c.get("/app/me").json()["paused"] is False


def test_resume_needs_the_owner(browser):
    assert browser().post("/app/resume").status_code in (401, 403)
