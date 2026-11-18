"""#410 over HTTP: the manifest for a chosen name, checking each pasted token without saving it, and the test message.

Owner only (also enumerated in guards/api/test_authz.py). Nothing is stored by a check and no token comes back."""

from __future__ import annotations

import httpx
import pytest
import yaml
from sqlalchemy import func, select

from superaide.app import connections
from superaide.channels.base import FakeMessenger
from superaide.channels.slack import scopes
from superaide.identity.auth.models import IntegrationSecret
from superaide.identity.models import ChannelIdentity, Person
from superaide.platform.db.session import session_scope
from superaide.proactive import notify
from tests.api_support import *  # noqa: F403
from tests.app.test_connections import TG_TOKEN, services

SLACK_BOT = "xoxb-1111-2222-abcdefghijklmnop"
SLACK_APP = "xapp-1-A111-2222-abcdefghijklmnop"
ALL_SCOPES = ",".join(scopes.REQUIRED_SCOPES)


@pytest.fixture
def network(monkeypatch) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "auth.test" in str(request.url):
            return httpx.Response(
                200, json={"ok": True, "team_id": "T123", "team": "Larkfield"}, headers={"x-oauth-scopes": ALL_SCOPES}
            )
        return services(request)

    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def stored() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(IntegrationSecret)) or 0


def test_the_manifest_comes_back_for_the_chosen_name(owner) -> None:
    r = owner.get("/app/connections/slack/manifest", params={"name": "Ava for Jordan"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ava for Jordan"
    assert yaml.safe_load(body["manifest"])["display_information"]["name"] == "Ava for Jordan"
    assert body["create_url"].startswith("https://api.slack.com/apps?new_app=1&manifest_yaml=")
    blank = owner.get("/app/connections/slack/manifest").json()
    assert blank["name"] == "Ava"


def test_checking_a_telegram_token_names_the_bot_and_stores_nothing(owner, network) -> None:
    r = owner.post("/app/connections/telegram/check", json={"bot_token": TG_TOKEN})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["username"] == "ava_for_jordan_bot"
    assert TG_TOKEN not in r.text and stored() == 0


def test_a_malformed_telegram_token_is_said_to_be_wrong_without_a_network_call(owner, network) -> None:
    r = owner.post("/app/connections/telegram/check", json={"bot_token": "not-a-token"})
    assert r.status_code == 200 and r.json()["ok"] is False and "doesn't look like a bot token" in r.json()["error"]
    assert network == [] and stored() == 0


def test_each_slack_token_is_checked_on_its_own(owner, network) -> None:
    only_bot = owner.post("/app/connections/slack/check", json={"bot_token": SLACK_BOT}).json()
    assert only_bot["bot"]["ok"] is True and only_bot["app"] is None
    only_app = owner.post("/app/connections/slack/check", json={"app_token": SLACK_APP}).json()
    assert only_app["app"] == {"ok": True} and only_app["bot"] is None
    wrong = owner.post("/app/connections/slack/check", json={"bot_token": "xoxp-nope", "app_token": "xoxb-nope"}).json()
    assert "xoxb-" in wrong["bot"]["error"] and "xapp-" in wrong["app"]["error"]
    assert stored() == 0
    for r in (only_bot, only_app, wrong):
        assert SLACK_BOT not in str(r) and SLACK_APP not in str(r)


def test_a_slack_bot_token_missing_a_scope_names_it(owner, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        granted = ALL_SCOPES.replace("chat:write,", "")
        return httpx.Response(200, json={"ok": True}, headers={"x-oauth-scopes": granted})

    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    got = owner.post("/app/connections/slack/check", json={"bot_token": SLACK_BOT}).json()["bot"]
    assert got["ok"] is False and got["missing_required"] == ["chat:write"] and "chat:write" in got["error"]


def test_a_check_with_no_token_is_refused_and_an_oversize_one_too(owner, network) -> None:
    assert owner.post("/app/connections/telegram/check", json={"bot_token": ""}).status_code == 422
    assert owner.post("/app/connections/slack/check", json={"bot_token": "x" * 301}).status_code == 422
    assert network == []


def link(provider: str, key: str, endpoint: str = "") -> None:
    with session_scope() as s:
        person = s.query(Person).first()
        s.add(
            ChannelIdentity(
                person_id=person.id, provider=provider, stable_key=key, trust_level=3, lifecycle="active",
                endpoint_key=endpoint,
            )
        )  # fmt: skip


def test_the_test_message_goes_only_to_the_owners_own_linked_account(owner, network, monkeypatch) -> None:
    sender = FakeMessenger()
    monkeypatch.setitem(notify.MESSENGER_FACTORIES, "telegram", lambda token: sender)
    assert owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN}).status_code == 200
    # Nobody linked yet: a plain instruction, and nothing is sent.
    r = owner.post("/app/connections/telegram/test-message")
    assert r.status_code == 409 and "Link your Telegram account first" in r.json()["detail"] and sender.sent == []
    link("telegram", "555000111", endpoint="123456789")
    r = owner.post("/app/connections/telegram/test-message")
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert len(sender.sent) == 1 and sender.sent[0]["stable_key"] == "555000111"
    assert "Telegram is connected" in sender.sent[0]["text"] and TG_TOKEN not in sender.sent[0]["text"]


def test_a_test_message_for_a_channel_that_is_not_connected_says_so(owner, network, monkeypatch) -> None:
    link("slack", "U0JORDAN1")
    r = owner.post("/app/connections/slack/test-message")
    assert r.status_code == 502 and "isn't connected any more" in r.json()["detail"]


def test_a_test_message_for_an_unknown_channel_is_404(owner) -> None:
    assert owner.post("/app/connections/whatsapp/test-message").status_code == 404


def test_a_channel_that_refuses_the_message_is_a_plain_error(owner, network, monkeypatch) -> None:
    class Refusing(FakeMessenger):
        def send_dm(self, identity, text):
            return False

    monkeypatch.setitem(notify.MESSENGER_FACTORIES, "telegram", lambda token: Refusing())
    owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN})
    link("telegram", "555000111", endpoint="123456789")
    r = owner.post("/app/connections/telegram/test-message")
    assert r.status_code == 502 and "didn't accept the message" in r.json()["detail"]


def test_only_the_owner_may_use_the_guided_connect(owner, other) -> None:
    for method, path, body in [
        ("get", "/app/connections/slack/manifest", None),
        ("post", "/app/connections/telegram/check", {"bot_token": TG_TOKEN}),
        ("post", "/app/connections/slack/check", {"bot_token": SLACK_BOT}),
        ("post", "/app/connections/telegram/test-message", None),
    ]:
        r = getattr(other, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 403, (path, r.status_code)
