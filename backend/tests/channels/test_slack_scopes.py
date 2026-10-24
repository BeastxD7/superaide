"""#447: startup and connect check the Slack scopes against a fake Slack API; a missing one is an ERROR log and a
notice on Connections, a rejected token is the existing "Token rejected", no token means no check."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
import yaml

from superaide.channels import health
from superaide.channels.slack import scopes
from superaide.platform.db.session import session_scope

BOT = "xoxb-1-abc-defghijk"
ALL = ",".join(scopes.REQUIRED_SCOPES)


def fake_slack(granted: str | None = ALL, ok: bool = True, error: str = "invalid_auth"):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "auth.test" in str(request.url)
        headers = {"x-oauth-scopes": granted} if granted is not None else {}
        body = {"ok": True, "team_id": "T123", "user": "ava"} if ok else {"ok": False, "error": error}
        return httpx.Response(200, json=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler))


def missing_now() -> list[str]:
    with session_scope() as s:
        return health.missing_scopes(s, "slack")


def test_all_scopes_present_means_no_error_and_no_notice(agent, caplog):
    with caplog.at_level(logging.ERROR):
        result = scopes.validate_and_record(BOT, fake_slack())
    assert result.status == scopes.OK and result.missing == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert missing_now() == []


def test_one_missing_scope_is_one_error_naming_it_and_a_notice(agent, caplog):
    granted = ALL.replace(",groups:history", "")
    with caplog.at_level(logging.ERROR):
        result = scopes.validate_and_record(BOT, fake_slack(granted))
    assert result.status == scopes.MISSING and result.missing == ["groups:history"]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "groups:history" in errors[0] and "reinstall" in errors[0]
    assert BOT not in errors[0]
    assert missing_now() == ["groups:history"]
    with session_scope() as s:
        assert health.get(s, "slack")["missing_scopes"] == ["groups:history"]
    # Reinstalled with the scope: the next check clears the notice.
    scopes.validate_and_record(BOT, fake_slack())
    assert missing_now() == []


def test_every_missing_scope_is_named(agent, caplog):
    with caplog.at_level(logging.ERROR):
        scopes.validate_and_record(BOT, fake_slack("chat:write"))
    msg = next(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert all(s in msg for s in scopes.REQUIRED_SCOPES if s != "chat:write") and "chat:write," not in msg


def test_a_rejected_token_is_token_rejected_not_a_scope_notice(agent, caplog):
    with caplog.at_level(logging.ERROR):
        result = scopes.validate_and_record(BOT, fake_slack(None, ok=False, error="token_revoked"))
    assert result.status == scopes.REJECTED
    with session_scope() as s:
        h = health.get(s, "slack")
    assert h["status"] == health.AUTH_FAILED and "token_revoked" in h["last_error"] and h["missing_scopes"] == []


def test_slack_not_configured_makes_no_call_and_no_notice(agent, caplog):
    def boom(request):
        raise AssertionError("no token: Slack must not be called")

    with caplog.at_level(logging.ERROR):
        result = scopes.validate_and_record("", httpx.Client(transport=httpx.MockTransport(boom)))
    assert result.status == scopes.UNKNOWN
    assert not caplog.records and missing_now() == []


def test_a_slack_that_does_not_list_scopes_or_is_unreachable_raises_no_alarm(agent, caplog):
    assert scopes.check_scopes(BOT, fake_slack(None)).status == scopes.UNKNOWN

    def down(request):
        raise httpx.ConnectError("offline")

    with caplog.at_level(logging.ERROR):
        r = scopes.validate_and_record(BOT, httpx.Client(transport=httpx.MockTransport(down)))
    assert r.status == scopes.UNREACHABLE and not [x for x in caplog.records if x.levelno >= logging.ERROR]


def test_connecting_with_a_missing_scope_shows_the_notice_on_connections(agent, monkeypatch):
    from fastapi.testclient import TestClient

    from superaide.app import connections
    from superaide.main import create_app

    def slack(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "auth.test" in url:
            return httpx.Response(
                200, json={"ok": True, "team_id": "T123", "team": "Acme", "user": "ava"},
                headers={"x-oauth-scopes": "chat:write,im:history"},
            )  # fmt: skip
        return httpx.Response(200, json={"ok": True, "url": "wss://example"})

    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(slack)))
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post(
        "/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": "correct horse battery"}
    )
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    r = c.post("/app/connections/slack", json={"bot_token": BOT, "app_token": "xapp-1-abc-defghi"})
    assert r.status_code == 200, r.text
    h = r.json()["services"]["slack"]["health"]
    assert "channels:history" in h["missing_scopes"] and "chat:write" not in h["missing_scopes"]
    assert "channels:history" in c.get("/app/me").json()["channels"]["health"]["slack"]["missing_scopes"]
    c.post("/app/connections/slack/disconnect")
    assert c.get("/app/me").json()["channels"]["health"]["slack"]["missing_scopes"] == []


@pytest.mark.parametrize("path", ["manifest.yml"])
def test_the_manifest_lists_exactly_the_scopes_the_check_wants(path):
    manifest = Path(scopes.__file__).parent / path
    listed = yaml.safe_load(manifest.read_text())["oauth_config"]["scopes"]["bot"]
    assert sorted(listed) == sorted(scopes.REQUIRED_SCOPES)
