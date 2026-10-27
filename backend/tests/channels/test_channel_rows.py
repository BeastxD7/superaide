"""Home's "Where people reach Ava" and Connections must agree (#282, #287, #289).

A channel is "available" while its bot/app is connected, from the app or .env (no network call: a made-up token
still counts), and the owner's own account only counts as linked while the bot/app is there.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from superaide.brain.llm import FakeLLM
from superaide.identity.models import ChannelIdentity, Person
from superaide.main import create_app
from superaide.pipeline import Agent
from superaide.platform.db.session import session_scope
from tests.conftest import OUR_TEAM

PASSWORD = "correct horse battery"


def owner_with(model, outbox, settings, **tokens) -> TestClient:
    conf = settings.model_copy(update={"slack_team_id": OUR_TEAM, **tokens})
    c = TestClient(
        create_app(Agent(llm=FakeLLM(model), messengers=outbox, settings=conf)),
        headers={"X-Requested-With": "superaide"},
    )
    assert (
        c.post(
            "/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def link_owner(provider: str, key: str) -> None:
    with session_scope() as s:
        person = s.query(Person).first()
        s.add(
            ChannelIdentity(person_id=person.id, provider=provider, stable_key=key, trust_level=3, lifecycle="active")
        )


def channels(c: TestClient) -> dict:
    return c.get("/app/me").json()["channels"]


def test_a_connected_telegram_bot_is_available_without_the_owner_linked_yet(model, outbox, settings):
    c = owner_with(model, outbox, settings, telegram_bot_token="123456789:fake-token-not-reachable")
    ch = channels(c)
    assert ch["telegram_available"] is True and ch["telegram"] is False


def test_no_telegram_bot_is_not_available(model, outbox, settings):
    c = owner_with(model, outbox, settings, telegram_bot_token="")
    assert channels(c)["telegram_available"] is False


@pytest.mark.parametrize(
    ("provider", "tokens"),
    [
        ("telegram", {"telegram_bot_token": "123456789:fake"}),
        (
            "slack",
            {
                "slack_bot_token": "-".join(["xoxb", "fake"]),
                "slack_app_token": "-".join(["xapp", "1", "A0APPONE", "1", "secret"]),
            },
        ),
    ],
)
def test_a_linked_account_counts_only_while_the_bot_or_app_is_connected(model, outbox, settings, provider, tokens):
    on = owner_with(model, outbox, settings, **tokens)
    link_owner(provider, "U0OWNER01")
    assert channels(on)[provider] is True and channels(on)[f"{provider}_available"] is True
    # The same owner after Disconnect: nothing can reach them there, so Home must not say "Linked to you".
    off = TestClient(
        create_app(
            Agent(llm=FakeLLM(model), messengers=outbox, settings=settings.model_copy(update={k: "" for k in tokens}))
        ),
        headers={"X-Requested-With": "superaide"},
    )
    assert off.post("/auth/login", json={"email": "s@example.com", "password": PASSWORD}).status_code == 200
    ch = channels(off)
    assert ch[provider] is False and ch[f"{provider}_available"] is False
