"""/app/tour (#626): the owner's answers to the guided tours: theirs only, validated, replayable."""

from __future__ import annotations

import pytest

from tests.api_support import OTHER, hire, signup

H = {"X-Requested-With": "superaide"}


@pytest.fixture
def owner(browser):
    c = browser()
    assert signup(c).status_code == 200 and hire(c).status_code == 200
    return c


def test_a_new_owner_has_seen_no_tour(owner):
    r = owner.get("/app/tour")
    assert r.status_code == 200
    assert r.json() == {"tours": {}}


def test_complete_skip_and_change_an_answer(owner):
    assert owner.post("/app/tour/welcome", json={"status": "skipped"}, headers=H).status_code == 200
    assert owner.post("/app/tour/chat", json={"status": "completed"}, headers=H).status_code == 200
    assert owner.get("/app/tour").json()["tours"] == {"welcome": "skipped", "chat": "completed"}
    assert owner.post("/app/tour/welcome", json={"status": "completed"}, headers=H).status_code == 200
    assert owner.get("/app/tour").json()["tours"]["welcome"] == "completed"


def test_reset_forgets_every_answer_so_the_tour_can_replay(owner):
    owner.post("/app/tour/welcome", json={"status": "completed"}, headers=H)
    owner.post("/app/tour/settings", json={"status": "skipped"}, headers=H)
    assert owner.post("/app/tour/reset", json={}, headers=H).status_code == 200
    assert owner.get("/app/tour").json() == {"tours": {}}


def test_unknown_tour_and_bad_status_are_refused(owner):
    assert owner.post("/app/tour/nope", json={"status": "completed"}, headers=H).status_code == 404
    assert owner.post("/app/tour/chat", json={"status": "pending"}, headers=H).status_code == 422
    assert owner.post("/app/tour/chat", json={}, headers=H).status_code == 422
    assert owner.get("/app/tour").json() == {"tours": {}}


def test_answers_belong_to_one_owner(browser, monkeypatch):
    from superaide.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    a.post("/app/tour/welcome", json={"status": "completed"}, headers=H)
    assert b.get("/app/tour").json() == {"tours": {}}
    b.post("/app/tour/reset", json={}, headers=H)  # B's replay never touches A's answers
    assert a.get("/app/tour").json()["tours"] == {"welcome": "completed"}


def test_signed_out_and_not_yet_hired_are_refused(browser):
    out = browser()
    assert out.get("/app/tour").status_code == 401
    assert out.post("/app/tour/chat", json={"status": "completed"}, headers=H).status_code == 401
    unhired = browser()
    assert signup(unhired).status_code == 200
    assert unhired.get("/app/tour").status_code == 403
    assert unhired.post("/app/tour/reset", json={}, headers=H).status_code == 403
