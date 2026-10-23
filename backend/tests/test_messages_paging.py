"""Chat history is paged: the newest messages first, and `before` reaches the older ones (#299)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from superaide.main import create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def browser(agent):
    app = create_app(agent)

    def make() -> TestClient:
        return TestClient(app, headers={"X-Requested-With": "superaide"})

    return make


def hired(browser, email: str) -> TestClient:
    c = browser()
    assert (
        c.post("/auth/signup", json={"name": "Shashank Rao", "email": email, "password": PASSWORD}).status_code == 200
    )
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def test_older_messages_can_be_reached_page_by_page(browser, model):
    owner = hired(browser, "s@example.com")
    for i in range(1, 6):
        assert owner.post("/app/chat", json={"text": f"message number {i}"}).status_code == 200
    everything = owner.get("/app/messages").json()
    assert len(everything) >= 11  # the greeting, five of yours and five replies

    newest = owner.get("/app/messages", params={"limit": 4}).json()
    assert [m["id"] for m in newest] == [m["id"] for m in everything[-4:]]  # oldest first within the page

    seen = newest
    while True:  # walk back to the very first message
        older = owner.get("/app/messages", params={"limit": 4, "before": seen[0]["id"]}).json()
        if not older:
            break
        assert len(older) <= 4
        assert not {m["id"] for m in older} & {m["id"] for m in seen}  # nothing twice
        seen = older + seen
    assert [m["id"] for m in seen] == [m["id"] for m in everything]
    assert seen[0]["from"] == "agent"  # the introduction greeting is reachable again


def test_before_never_reaches_into_someone_elses_conversation(browser, model):
    owner = hired(browser, "s@example.com")
    other = hired(browser, "t@example.com")
    owner.post("/app/chat", json={"text": "private to me"})
    theirs = other.get("/app/messages").json()
    mine = owner.get("/app/messages").json()
    assert owner.get("/app/messages", params={"before": theirs[-1]["id"]}).json() == []
    assert owner.get("/app/messages", params={"before": "00000000-0000-4000-8000-0000000000ff"}).json() == []
    assert owner.get("/app/messages", params={"before": mine[-1]["id"]}).json() != []
    assert owner.get("/app/messages", params={"before": "not-a-uuid"}).status_code == 422
