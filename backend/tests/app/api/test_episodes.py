"""The episode reading view (/app/episodes, #50): the owner's summaries, nobody else's, flag-gated and erasable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.identity.models import Person
from superaide.memory.models import Context, Episode, Event
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from tests.api_support import OTHER, OWNER, hire, signup
from tests.tenancy.api.test_many_owners import account


@pytest.fixture(autouse=True)
def many_owners_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    monkeypatch.setattr(get_settings(), "sleep_job_enabled", True)


def owners(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    return a, b


def seed(email: str, summary: str, audience: list[uuid.UUID] | None = None, unknown: bool = False) -> str:
    owner_id = account(email).person_id
    with session_scope() as s:
        c = Context(
            provider="telegram",
            tenant_scope="",
            external_id=str(uuid.uuid4()),
            kind="dm",
            audience_class="private_1to1",
        )
        s.add(c)
        s.flush()
        e = Event(context_id=c.id, direction="in", content="hello", audience_person_ids=[owner_id])
        s.add(e)
        s.flush()
        now = datetime.now(UTC)
        ep = Episode(
            owner_person_id=owner_id, context_id=c.id, channel="telegram", event_ids=[e.id],
            audience_person_ids=audience if audience is not None else [owner_id], audience_has_unknown=unknown,
            started_at=now - timedelta(hours=9), ended_at=now - timedelta(hours=8), summary=summary,
            hints=["lisbon"],
        )  # fmt: skip
        s.add(ep)
        s.flush()
        return str(ep.id)


def test_the_owner_sees_only_their_own_summaries(browser):
    a, b = owners(browser)
    seed(OWNER["email"], "Planned the Lisbon offsite.")
    seed(OTHER["email"], "SECRET-OF-THE-OTHER-OWNER")
    r = a.get("/app/episodes")
    assert r.status_code == 200
    assert [e["summary"] for e in r.json()["episodes"]] == ["Planned the Lisbon offsite."]
    assert "SECRET-OF-THE-OTHER-OWNER" not in r.text
    assert "Lisbon" not in b.get("/app/episodes").text  # and the other owner sees none of it


def test_a_summary_whose_audience_excludes_the_owner_is_never_listed(browser):
    a, _ = owners(browser)
    with session_scope() as s:
        stranger = Person(display_name="Priya Tan", relation="contact")
        s.add(stranger)
        s.flush()
        stranger_id = stranger.id
    seed(OWNER["email"], "A private chat between others.", audience=[stranger_id])
    shared = seed(OWNER["email"], "A group chat.", audience=[account(OWNER["email"]).person_id, stranger_id])
    got = {e["summary"]: e for e in a.get("/app/episodes").json()["episodes"]}
    assert "A private chat between others." not in got and got["A group chat."]["others_present"] is True
    assert got["A group chat."]["id"] == shared


def test_it_answers_404_while_the_sleep_job_is_off(browser, monkeypatch):
    a, _ = owners(browser)
    seed(OWNER["email"], "Planned the Lisbon offsite.")
    monkeypatch.setattr(get_settings(), "sleep_job_enabled", False)
    assert a.get("/app/episodes").status_code == 404


def test_erasing_removes_the_summary_but_not_the_messages_and_checks_ownership(browser):
    a, b = owners(browser)
    mine = seed(OWNER["email"], "Planned the Lisbon offsite.")
    assert b.post(f"/app/episodes/{mine}/erase").status_code == 404
    assert a.post(f"/app/episodes/{uuid.uuid4()}/erase").status_code == 404
    assert a.post(f"/app/episodes/{mine}/erase").status_code == 200
    assert a.get("/app/episodes").json()["episodes"] == []
    with session_scope() as s:
        assert s.scalar(select(Episode.id)) is None and s.scalar(select(Event.id)) is not None


def test_the_episode_routes_need_the_owner(browser):
    anonymous, unhired = browser(), browser()
    signup(unhired, name="Mallory Tan", email="mallory@example.com")
    for method, path in (("GET", "/app/episodes"), ("POST", f"/app/episodes/{uuid.uuid4()}/erase")):
        assert anonymous.request(method, path).status_code == 401
        assert unhired.request(method, path).status_code == 403
