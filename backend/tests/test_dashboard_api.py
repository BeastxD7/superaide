"""Read endpoints behind the owner's dashboard: people and standing preferences."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from superaide.main import create_app
from tests.test_app import PASSWORD, _answer, _hire


@pytest.fixture
def browser(agent):
    app = create_app(agent)
    return lambda: TestClient(app, headers={"X-Requested-With": "superaide"})


def _introduced(client) -> None:
    _hire(client)
    _answer(client, None, "hello", choice="What will you do for me?")
    _answer(client, None, "about", mode="chat", text="I run an AI consultancy in Singapore.")
    _answer(
        client,
        None,
        "people",
        people=[
            {"name": "Arjun", "relationship": "Team", "vip": False},
            {"name": "Priya", "relationship": "Partner", "vip": True},
        ],
    )
    _answer(client, None, "urgent", topics=["A family emergency"], other="Series A news", digest="morning")
    _answer(client, None, "reach", channel="app", quiet_start="22:30", quiet_end="07:00", timezone="Asia/Singapore")
    _answer(client, None, "share", shareable=["availability"], never="My home address")


def test_people_lists_vips_first(browser):
    client = browser()
    _introduced(client)
    people = client.get("/app/people").json()["people"]
    assert [p["name"] for p in people] == ["Priya", "Arjun"]
    assert people[0]["vip"] is True and people[0]["relationship"] == "partner" and people[0]["channels"] == []


def test_preferences_show_the_owners_rules(browser):
    client = browser()
    _introduced(client)
    pref = client.get("/app/preferences").json()
    assert pref["quiet_start"] == "22:30" and pref["digest"] == "morning" and pref["timezone"] == "Asia/Singapore"
    assert "Series A news" in pref["urgent_topics"]
    assert pref["shareable"][0]["value"] == "availability" and pref["shareable"][0]["label"]
    assert pref["never_share"] == "My home address"


def test_only_the_owner_can_read_them(browser):
    owner = browser()
    _introduced(owner)
    stranger = browser()
    stranger.post("/auth/signup", json={"name": "Mallory", "email": "m@example.com", "password": PASSWORD})
    for path in ("/app/people", "/app/preferences"):
        r = stranger.get(path)
        assert r.status_code == 403
        assert "Priya" not in r.text and "home address" not in r.text
    signed_out = browser()
    assert signed_out.get("/app/people").status_code == 401
