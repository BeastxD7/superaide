"""Skill Ratchet (#120): drafts never run, only owner-edited playbooks go live, 50 at most, losers retire."""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from superaide.identity.auth.models import UserAccount
from superaide.identity.models import Person
from superaide.platform.db.session import session_scope
from superaide.trust import ratchet
from superaide.trust.models import Playbook


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "skill_ratchet_enabled", True)


@pytest.fixture
def drafting(agent):
    def responder(purpose, system, messages, output_model):
        assert purpose == "playbook"
        return ratchet.Draft(
            title="Board update",
            body="1. Lead with the number.\n2. Three bullets.",
            tools=["lookup_notes", "send_email"],
        )

    agent.llm.responder = responder


def owner_person():
    with session_scope() as s:
        return s.scalar(select(UserAccount.person_id))


def draft(owner) -> dict:
    r = owner.post("/app/playbooks", json={"task": "Write my board updates"})
    assert r.status_code == 200, r.text
    return r.json()


def test_off_by_default(owner):
    assert owner.get("/app/playbooks").status_code == 404
    assert owner.post("/app/playbooks", json={"task": "anything"}).status_code == 404


def test_a_draft_never_runs_and_drops_tools_it_may_not_have(owner, on, drafting):
    d = draft(owner)
    assert d["status"] == "draft" and d["tools"] == ["lookup_notes"]  # send_email is not a playbook tool
    with session_scope() as s:
        assert ratchet.live_text(s, s.get(Person, owner_person())) == ""


def test_only_an_owner_edited_playbook_goes_live(owner, on, drafting):
    d = draft(owner)
    same = owner.post(f"/app/playbooks/{d['id']}/activate", json={"body": d["drafted_body"]})
    assert same.status_code == 422 and "your own words" in same.json()["detail"]
    ok = owner.post(
        f"/app/playbooks/{d['id']}/activate", json={"body": "1. Lead with the number.\n2. Two bullets, my tone."}
    )
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    with session_scope() as s:
        text_ = ratchet.live_text(s, s.get(Person, owner_person()))
        assert "Two bullets, my tone." in text_ and "Three bullets" not in text_


def test_the_database_itself_refuses_an_active_playbook_nobody_approved(owner, on, drafting):
    d = draft(owner)
    with pytest.raises(DBAPIError), session_scope() as s:
        s.execute(text("UPDATE playbook SET status = 'active' WHERE id = :i"), {"i": d["id"]})


def test_at_most_fifty_active_and_the_database_enforces_it(owner, on, drafting):
    me = owner_person()
    with session_scope() as s:
        for i in range(ratchet.MAX_ACTIVE_PLAYBOOKS):
            s.add(
                Playbook(
                    owner_person_id=me,
                    title=f"p{i}",
                    drafted_body="a",
                    body="b",
                    status="active",
                    approved_by_person_id=me,
                )
            )
    d = draft(owner)
    r = owner.post(f"/app/playbooks/{d['id']}/activate", json={"body": "my own words"})
    assert r.status_code == 409 and "At most 50" in r.json()["detail"]
    with pytest.raises(DBAPIError), session_scope() as s:  # bypassing the code does not help
        s.add(
            Playbook(
                owner_person_id=me, title="x", drafted_body="a", body="b", status="active", approved_by_person_id=me
            )
        )
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Playbook).where(Playbook.status == "active")) == 50


def test_a_playbook_that_does_not_help_retires_itself(owner, on, drafting):
    d = draft(owner)
    owner.post(f"/app/playbooks/{d['id']}/activate", json={"body": "my own words"})
    for _ in range(ratchet.MIN_TRIALS):
        owner.post(f"/app/playbooks/{d['id']}/outcome", json={"used": True, "distance": 0.6})
        r = owner.post(f"/app/playbooks/{d['id']}/outcome", json={"used": False, "distance": 0.4})
    assert r.json()["status"] == "retired" and "lost its A/B" in r.json()["retired_reason"]
    with session_scope() as s:
        assert ratchet.live_text(s, s.get(Person, owner_person())) == ""


def test_a_playbook_that_helps_stays(owner, on, drafting):
    d = draft(owner)
    owner.post(f"/app/playbooks/{d['id']}/activate", json={"body": "my own words"})
    for _ in range(ratchet.MIN_TRIALS):
        owner.post(f"/app/playbooks/{d['id']}/outcome", json={"used": True, "distance": 0.1})
        r = owner.post(f"/app/playbooks/{d['id']}/outcome", json={"used": False, "distance": 0.5})
    assert r.json()["status"] == "active"


def test_the_coin_flip_is_a_fair_coin():
    rng = random.Random(7)
    flips = [ratchet.use_for_task(rng) for _ in range(1000)]
    assert 400 < sum(flips) < 600
    assert ratchet.edit_distance("same", "same") == 0.0 and ratchet.edit_distance("abc", "xyz") == 1.0


def test_someone_elses_or_unknown_playbooks_are_404(owner, on):
    assert owner.post(f"/app/playbooks/{uuid.uuid4()}/activate", json={"body": "x"}).status_code == 404
    assert owner.post(f"/app/playbooks/{uuid.uuid4()}/retire").status_code == 404
