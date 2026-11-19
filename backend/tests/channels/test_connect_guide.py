"""#410: the manifest made for the owner's chosen name, and every way a pasted token can be wrong, each naming the
exact scope or setting to fix. A fake Slack and Telegram (httpx MockTransport); no token is ever echoed."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import yaml

from superaide.channels import connect_guide as guide
from superaide.channels.slack import scopes

BOT = "xoxb-1111-2222-abcdefghijklmnop"
APP = "xapp-1-A111-2222-abcdefghijklmnop"
TG = "123456789:" + "A" * 35
ALL = ",".join([*scopes.REQUIRED_SCOPES, *scopes.AMBIENT_SCOPES])


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def slack(auth=None, conn=None, granted: str | None = ALL):
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth.test" in str(request.url):
            body = auth or {"ok": True, "team_id": "T1"}
            return httpx.Response(200, json=body, headers={"x-oauth-scopes": granted} if granted is not None else {})
        return httpx.Response(200, json=conn or {"ok": True})

    return client(handler)


# --- the manifest -------------------------------------------------------------------------------------------------


def test_the_manifest_is_a_valid_slack_manifest_with_the_scopes_events_and_socket_mode() -> None:
    m = yaml.safe_load(guide.render_manifest("Ava"))
    assert set(m) >= {"display_information", "features", "oauth_config", "settings"}
    assert m["settings"]["socket_mode_enabled"] is True
    assert m["features"]["bot_user"]["always_online"] is True
    assert m["features"]["app_home"]["messages_tab_enabled"] is True
    bot_scopes = m["oauth_config"]["scopes"]["bot"]
    assert len(bot_scopes) == len(set(bot_scopes)), "a scope is listed twice"
    # The manifest and the permission check are one list: a scope Ava checks for is a scope the app asks for.
    assert set(bot_scopes) == set(scopes.REQUIRED_SCOPES) | set(scopes.AMBIENT_SCOPES)
    events = set(m["settings"]["event_subscriptions"]["bot_events"])
    assert {"app_mention", "message.im", "message.channels", "message.groups"} <= events
    assert "request_url" not in m["settings"]["event_subscriptions"], "Socket Mode needs no public URL"


def test_every_optional_scope_is_a_scope_the_manifest_requests() -> None:
    for s in guide.OPTIONAL_SCOPES:
        assert s in scopes.REQUIRED_SCOPES


def test_the_chosen_name_is_the_app_name_and_the_bot_name() -> None:
    m = yaml.safe_load(guide.render_manifest("Ava for Jordan"))
    assert m["display_information"]["name"] == "Ava for Jordan"
    assert m["features"]["bot_user"]["display_name"] == "Ava for Jordan"


@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        (None, "Ava"),
        ("", "Ava"),
        ("   ", "Ava"),
        ("<script>alert(1)</script>", "scriptalert1script"),
        ("Meera\nscopes: [admin]", "Meera scopes admin"),
        ("x" * 80, "x" * 35),
        ("  Priya   Tan  ", "Priya Tan"),
        ("Ananya's Ava", "Ananya's Ava"),
    ],
)
def test_a_typed_name_is_cleaned_before_it_goes_into_the_manifest(typed, kept) -> None:
    assert guide.clean_name(typed) == kept
    m = yaml.safe_load(guide.render_manifest(typed))
    assert m["display_information"]["name"] == kept
    # whatever was typed, the scopes are exactly the checked-in ones
    assert set(m["oauth_config"]["scopes"]["bot"]) == set(scopes.REQUIRED_SCOPES) | set(scopes.AMBIENT_SCOPES)


def test_the_create_link_carries_the_manifest_for_that_name() -> None:
    url = guide.create_url("Rahul's Ava")
    parsed = urlparse(url)
    assert parsed.netloc == "api.slack.com" and parse_qs(parsed.query)["new_app"] == ["1"]
    carried = yaml.safe_load(parse_qs(parsed.query)["manifest_yaml"][0])
    assert carried["display_information"]["name"] == "Rahul's Ava"


# --- Telegram -----------------------------------------------------------------------------------------------------


def test_a_telegram_token_that_works_names_the_bot() -> None:
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"username": "ava_bot", "first_name": "Ava"}})

    assert guide.check_telegram_token(TG, client(handler)) == {"ok": True, "username": "ava_bot", "name": "Ava"}


@pytest.mark.parametrize(
    ("token", "handler", "says"),
    [
        ("nope", None, "doesn't look like a bot token"),
        (TG, lambda r: httpx.Response(200, json={"ok": False}), "didn't accept that token"),
        (TG, lambda r: httpx.Response(200, json={"ok": True, "result": "x"}), "without the bot's details"),
        (TG, lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")), "Couldn't reach Telegram"),
    ],
)
def test_each_way_a_telegram_token_fails_says_what_to_do(token, handler, says) -> None:
    got = guide.check_telegram_token(token, client(handler) if handler else None)
    assert got["ok"] is False and says in got["error"]
    assert token not in got["error"]


# --- Slack bot token ----------------------------------------------------------------------------------------------


def test_a_complete_bot_token_is_ok_with_nothing_missing() -> None:
    assert guide.check_slack_bot_token(BOT, slack()) == {
        "ok": True, "missing_required": [], "missing_optional": [], "scopes_known": True,
    }  # fmt: skip


def test_missing_scopes_are_named_and_split_into_needed_and_optional() -> None:
    granted = ",".join(s for s in scopes.REQUIRED_SCOPES if s not in ("chat:write", "channels:history", "files:read"))
    got = guide.check_slack_bot_token(BOT, slack(granted=granted))
    assert got["ok"] is False
    assert got["missing_required"] == ["chat:write"]
    assert set(got["missing_optional"]) >= {"channels:history", "files:read"}
    assert "chat:write" in got["error"] and "reinstall" in got["error"]


def test_only_optional_scopes_missing_is_still_ok_and_listed() -> None:
    granted = ",".join(s for s in scopes.REQUIRED_SCOPES if s not in ("groups:history", "channels:history"))
    got = guide.check_slack_bot_token(BOT, slack(granted=granted))
    assert got["ok"] is True and got["missing_required"] == []
    assert set(got["missing_optional"]) >= {"groups:history", "channels:history"}


def test_when_slack_does_not_list_scopes_no_problem_is_invented() -> None:
    got = guide.check_slack_bot_token(BOT, slack(granted=None))
    assert got["ok"] is True and got["scopes_known"] is False


@pytest.mark.parametrize(
    ("token", "auth", "says"),
    [
        ("xoxp-user-token", None, "should start with xoxb-"),
        (BOT, {"ok": False, "error": "invalid_auth"}, "Install App"),
        (BOT, {"ok": False, "error": "token_revoked"}, "revoked"),
        (BOT, {"ok": False, "error": "account_inactive"}, "reinstall the app"),
        (BOT, {"ok": False, "error": "something_new"}, "something_new"),
    ],
)
def test_each_way_a_slack_bot_token_fails_says_what_to_do(token, auth, says) -> None:
    got = guide.check_slack_bot_token(token, slack(auth=auth))
    assert got["ok"] is False and says in got["error"]
    assert token not in got["error"]


def test_slack_unreachable_is_said_plainly() -> None:
    def down(request):
        raise httpx.ConnectError("down")

    got = guide.check_slack_bot_token(BOT, client(down))
    assert got["ok"] is False and "Couldn't reach Slack" in got["error"]
    got = guide.check_slack_app_token(APP, client(down))
    assert got["ok"] is False and "Couldn't reach Slack" in got["error"]


# --- Slack app-level token ----------------------------------------------------------------------------------------


def test_a_good_app_token_is_ok() -> None:
    assert guide.check_slack_app_token(APP, slack()) == {"ok": True}


@pytest.mark.parametrize(
    ("token", "conn", "says"),
    [
        ("xoxb-bot-not-app", None, "should start with xapp-"),
        (APP, {"ok": False, "error": "missing_scope"}, "connections:write"),
        (APP, {"ok": False, "error": "not_allowed_token_type"}, "not an app-level token"),
        (APP, {"ok": False, "error": "invalid_auth"}, "Basic Information > App-Level Tokens"),
        (APP, {"ok": False, "error": "token_revoked"}, "Basic Information > App-Level Tokens"),
        (APP, {"ok": False, "error": "odd_code"}, "Socket Mode is on"),
    ],
)
def test_each_way_a_slack_app_token_fails_names_the_scope_or_setting(token, conn, says) -> None:
    got = guide.check_slack_app_token(token, slack(conn=conn))
    assert got["ok"] is False and says in got["error"]
    assert token not in got["error"]


def test_split_missing_puts_ambient_listing_scopes_with_the_optional_ones() -> None:
    required, optional = guide.split_missing(["chat:write", "channels:read", "files:read"])
    assert required == ["chat:write"] and optional == ["channels:read", "files:read"]
