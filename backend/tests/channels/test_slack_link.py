"""The owner proves who they are on Slack: a code made in the signed-in app, typed in a direct message.

Telegram has a deep link; Slack has none, so the owner sends `link <code>` to Ava in a DM. The code is
single-use, short-lived, bound to the connected Slack app, redeemed only in a DM and never repeated.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.brain.llm import FakeLLM
from superaide.channels.base import InboundMessage
from superaide.channels.slack import parse_event
from superaide.identity.trust import Trust
from superaide.main import create_app
from superaide.pipeline import SLACK_BAD_CODES_PER_HOUR, Agent
from superaide.platform.db.models import AuditLog, ChannelIdentity, ChannelInvite, Event, IdentityLinkEvent, Person
from superaide.platform.db.session import session_scope
from tests.conftest import OUR_TEAM

PASSWORD = "correct horse battery"
APP_TOKEN = "xapp-1-A0APPONE-123456-secretsecret"
APP = "A0APPONE"
OTHER_APP = "A0APPTWO"
ME = "U0OWNER01"
_ids = itertools.count(1)


@pytest.fixture
def slack_agent(model, outbox, settings) -> Agent:
    conf = settings.model_copy(
        update={"slack_bot_token": "xoxb-fake", "slack_app_token": APP_TOKEN, "slack_team_id": OUR_TEAM}
    )
    return Agent(llm=FakeLLM(model), messengers=outbox, settings=conf)


@pytest.fixture
def app(slack_agent) -> TestClient:
    c = TestClient(create_app(slack_agent), headers={"X-Requested-With": "superaide"}, raise_server_exceptions=False)
    body = {"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD}
    assert c.post("/auth/signup", json=body).status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def slm(user: str, text: str, *, app_id: str = APP, kind: str = "im", user_team: str = OUR_TEAM) -> InboundMessage:
    """A message as the Slack adapter parses it, so the endpoint key is the real "<team>/<app>"."""
    n = next(_ids)
    payload = {
        "team_id": OUR_TEAM,
        "api_app_id": app_id,
        "event": {
            "type": "message" if kind == "im" else "app_mention",
            "user": user,
            "text": text,
            "channel": f"D{user}" if kind == "im" else "C0GENERAL",
            "channel_type": kind,
            "ts": f"1700000000.{n:06d}",
            "user_team": user_team,
        },
    }
    msg = parse_event(payload, our_team_id=OUR_TEAM, verified=True)
    assert msg is not None
    return msg


def make_code(app: TestClient) -> str:
    r = app.post("/app/channels/slack/invite")
    assert r.status_code == 200, r.text
    return r.json()["code"]


def identity(user: str = ME) -> ChannelIdentity | None:
    with session_scope() as s:
        row = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == user))
        if row:
            s.expunge(row)
        return row


def audits(action: str) -> list[dict]:
    with session_scope() as s:
        return [a.detail for a in s.scalars(select(AuditLog).where(AuditLog.action == action)).all()]


def test_the_app_makes_a_code_and_says_what_to_send(app):
    r = app.post("/app/channels/slack/invite")
    assert r.status_code == 200
    body = r.json()
    assert f"link {body['code']}" in body["instruction"] and "direct message" in body["instruction"]
    assert body["expires_minutes"] == 15
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(UTC)
    me = app.get("/app/me").json()["channels"]
    assert me["slack_available"] is True and me["slack"] is False
    assert audits("channel_invite_created")[-1]["provider"] == "slack"
    assert body["code"] not in str(audits("channel_invite_created"))  # only a hash is stored


def test_no_slack_app_connected_is_a_404(model, outbox, settings):
    bare = Agent(llm=FakeLLM(model), messengers=outbox, settings=settings.model_copy(update={"slack_app_token": ""}))
    c = TestClient(create_app(bare), headers={"X-Requested-With": "superaide"}, raise_server_exceptions=False)
    c.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD})
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    assert c.post("/app/channels/slack/invite").status_code == 404
    assert c.get("/app/me").json()["channels"]["slack_available"] is False


def test_the_code_in_a_dm_links_the_owner(app, slack_agent):
    code = make_code(app)
    assert slack_agent.handle(slm(ME, "hello")).mode == "stranger"
    reply = slack_agent.handle(slm(ME, f"  LiNk {code}  ")).reply or ""
    assert reply.startswith("Linked. Hi Jordan")
    me = identity()
    assert me and me.trust_level == int(Trust.LINKED) and me.person_id is not None
    assert me.endpoint_key == f"{OUR_TEAM}/{APP}"
    with session_scope() as s:
        event = s.scalar(select(IdentityLinkEvent).where(IdentityLinkEvent.identity_id == me.id))
        assert event and event.method == "invite_from_app"
        stored = " ".join(e.content for e in s.scalars(select(Event)).all())
    assert code not in stored  # the code is not kept in the message log
    assert app.get("/app/me").json()["channels"]["slack"] is True
    # and from now on she knows who it is
    assert slack_agent.handle(slm(ME, "hi again")).mode == "known"


def test_a_code_is_single_use_and_a_second_account_cannot_take_it(app, slack_agent):
    code = make_code(app)
    assert "Linked" in (slack_agent.handle(slm(ME, f"link {code}")).reply or "")
    late = slack_agent.handle(slm("U0IMPOST1", f"link {code}")).reply or ""
    assert "expired or was already used" in late
    other = identity("U0IMPOST1")
    assert other and other.person_id is None


def test_the_same_words_in_a_channel_or_thread_do_nothing_and_are_not_echoed(app, slack_agent, outbox):
    code = make_code(app)
    for text in (f"link {code}", f"<@U0BOT> link {code}"):
        result = slack_agent.handle(slm(ME, text, kind="channel"))
        assert code not in (result.reply or "")
    sent = " ".join(m["text"] for m in outbox["slack"].sent)
    assert code not in sent
    with session_scope() as s:
        assert code not in " ".join(e.content for e in s.scalars(select(Event)).all())
        assert s.scalar(select(ChannelInvite.used_at)) is None
    me = identity()
    assert me is None or me.person_id is None
    # the code still works in a DM afterwards (it was never consumed)
    assert "Linked" in (slack_agent.handle(slm(ME, f"link {code}")).reply or "")


def test_expired_wrong_and_other_app_codes_get_the_neutral_reply(app, slack_agent):
    code = make_code(app)
    neutral = "expired or was already used"
    assert neutral in (slack_agent.handle(slm(ME, "link NOTAREALCODE12345")).reply or "")
    # a different Slack app in the same workspace is a different endpoint
    assert neutral in (slack_agent.handle(slm(ME, f"link {code}", app_id=OTHER_APP)).reply or "")
    assert any(d.get("reason") == "wrong_endpoint" for d in audits("invite_rejected"))
    with session_scope() as s:
        for invite in s.scalars(select(ChannelInvite)).all():
            invite.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert neutral in (slack_agent.handle(slm(ME, f"link {code}")).reply or "")
    me = identity()
    assert me and me.person_id is None


def test_a_slack_connect_user_from_another_workspace_is_refused(app, slack_agent):
    code = make_code(app)
    reply = slack_agent.handle(slm("U0EXTERN1", f"link {code}", user_team="T0OTHERTEAM")).reply or ""
    assert "expired or was already used" in reply
    ext = identity("U0EXTERN1")
    assert ext and ext.person_id is None
    assert audits("link_refused")


def test_too_many_wrong_codes_and_the_account_is_ignored(app, slack_agent):
    code = make_code(app)
    for i in range(SLACK_BAD_CODES_PER_HOUR):
        assert "expired" in (slack_agent.handle(slm(ME, f"link WRONGCODE{i:04d}")).reply or "")
    silent = slack_agent.handle(slm(ME, f"link {code}"))  # even the right one, now
    assert silent.reply is None
    assert audits("slack_link_attempt_ignored")
    assert identity() is not None and identity().person_id is None  # type: ignore[union-attr]
    assert len(audits("slack_link_attempt")) == SLACK_BAD_CODES_PER_HOUR  # every try is on record
    # another account is not held back by this one's misses
    assert "Linked" in (slack_agent.handle(slm("U0OWNER02", f"link {code}")).reply or "")


def test_linked_people_chatting_about_a_link_are_not_intercepted(app, slack_agent):
    assert "Linked" in (slack_agent.handle(slm(ME, f"link {make_code(app)}")).reply or "")
    result = slack_agent.handle(slm(ME, "link calendar"))
    assert result.mode == "known" and "expired" not in (result.reply or "")
    assert Person  # owner still the same
