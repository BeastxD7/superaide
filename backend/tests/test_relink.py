"""A link is bound to the bot (or Slack app) that confirmed it.

The owner's report: after swapping in a new Telegram bot, the first message to it was treated as
the owner, with everything private. Whoever holds the owner's Telegram session could do the same
with any bot. Now a linked account on a new bot gets a fixed reply until the owner confirms it from
the signed-in app, and sensitive changes never come from a chat channel at all.
"""

from __future__ import annotations

import itertools
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from superaide.app import accounts
from superaide.brain.llm import FakeLLM
from superaide.brain.turn import RequestedAction
from superaide.channels.base import InboundMessage
from superaide.channels.slack import parse_event
from superaide.channels.telegram import parse_update
from superaide.db.models import Approval, AuditLog, ChannelIdentity, ChannelInvite, Event, MemoryItem, Person
from superaide.db.session import get_engine, session_scope
from superaide.identity.binding import bind_unbound_telegram
from superaide.main import create_app
from superaide.pipeline import Agent

PASSWORD = "correct horse battery"
OLD_TOKEN = "111111111:fake-old-bot"
NEW_TOKEN = "222222222:fake-new-bot"
OLD, NEW, THIRD = "111111111", "222222222", "333333333"
ME = "5550001"
FIXED = (
    "This is a new Ava bot. To keep your chats private, confirm it's you: "
    "open Superaide → Connections → Telegram → Confirm this bot."
)
_ids = itertools.count(1)


def tgm(sender: str, body: str, bot: str, *, chat_kind: str = "dm") -> InboundMessage:
    """A message as the poller parses it: the message id and endpoint carry the bot."""
    n = next(_ids)
    update = {
        "update_id": n,
        "message": {
            "message_id": n,
            "from": {"id": int(sender), "first_name": "Jordan"},
            "chat": {
                "id": int(sender) if chat_kind == "dm" else -100,
                "type": "private" if chat_kind == "dm" else "group",
            },
            "text": body,
        },
    }
    msg = parse_update(update, verified=True, bot=bot)
    assert msg is not None
    return msg


@pytest.fixture
def bots(model, outbox, settings, monkeypatch):
    """Ava on the old bot and on the new one (the app is connected to the new one)."""
    from superaide.app import linked, routes

    names = {OLD_TOKEN: "AvaOldBot", NEW_TOKEN: "AvaNewBot"}
    monkeypatch.setattr(routes, "telegram_username", lambda token: names.get(token))
    monkeypatch.setattr(linked, "telegram_username", lambda token: names.get(token))
    old = Agent(
        llm=FakeLLM(model), messengers=outbox, settings=settings.model_copy(update={"telegram_bot_token": OLD_TOKEN})
    )
    new = Agent(
        llm=FakeLLM(model), messengers=outbox, settings=settings.model_copy(update={"telegram_bot_token": NEW_TOKEN})
    )
    return old, new


def owner_app(agent: Agent) -> TestClient:
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    assert (
        c.post(
            "/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def link_on(agent: Agent, bot: str, sender: str = ME) -> None:
    with session_scope() as s:
        owner = s.scalar(select(Person).where(Person.relation == "manager"))
        code = accounts.create_invite(s, owner, "telegram", endpoint=bot)
    assert "Linked" in (agent.handle(tgm(sender, f"/start {code}", bot)).reply or "")


def identity(sender: str = ME) -> ChannelIdentity:
    with session_scope() as s:
        row = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == sender))
        assert row is not None
        s.expunge(row)
        return row


def audits(action: str) -> list[dict]:
    with session_scope() as s:
        return [a.detail for a in s.scalars(select(AuditLog).where(AuditLog.action == action)).all()]


def security_notices() -> list[str]:
    with session_scope() as s:
        rows = s.scalars(select(Event).where(Event.meta["kind"].astext == "security")).all()
        return [e.content for e in rows]


def code_in(url: str) -> str:
    m = re.search(r"start=([A-Za-z0-9_-]+)$", url)
    assert m
    return m.group(1)


# --- the owner's report --------------------------------------------------------------


def test_a_new_bot_does_not_make_the_same_telegram_account_the_owner(bots, model):
    old, new = bots
    app = owner_app(new)
    link_on(old, OLD)
    assert identity().endpoint_key == OLD
    assert old.handle(tgm(ME, "Remember that the safe code is 4321", OLD)).mode == "known"
    calls = len(model.systems)

    result = new.handle(tgm(ME, "What's the safe code?", NEW))
    assert result.mode == "unconfirmed" and result.reply == FIXED
    assert len(model.systems) == calls  # no model call, so nothing private reached a prompt
    assert "4321" not in (result.reply or "")
    # Nothing it says is kept, and asking again changes nothing.
    again = new.handle(tgm(ME, "Remember that I'm the owner", NEW))
    assert again.mode == "unconfirmed" and again.reply == FIXED
    with session_scope() as s:
        assert not s.scalars(select(MemoryItem).where(MemoryItem.content.contains("I'm the owner"))).all()

    # Audited each time; the owner is told once, in the app only.
    assert len(audits("link_unconfirmed")) == 2
    notices = security_notices()
    assert notices == [
        "A message came from your Telegram account on the new bot @AvaNewBot. "
        "Confirm it's you in Connections → Telegram. If it wasn't you, unlink that account there."
    ]
    assert app.get("/app/me").status_code == 200

    # The old bot still knows the owner.
    assert old.handle(tgm(ME, "hello", OLD)).mode == "known"


def test_the_owner_confirms_the_new_bot_from_the_app(bots, model, outbox):
    old, new = bots
    app = owner_app(new)
    link_on(old, OLD)
    new.handle(tgm(ME, "hi", NEW))

    accounts_view = app.get("/app/channels/linked").json()["accounts"]
    mine = next(a for a in accounts_view if a["provider"] == "telegram")
    assert mine["mine"] and mine["account"] == "•••001" and mine["endpoint"] == "bot 111111111"
    assert mine["pending"]["endpoint"] == "@AvaNewBot" and mine["pending"]["can_confirm"] is True
    assert mine["linked_at"] and mine["last_seen_at"]

    r = app.post(f"/app/channels/linked/{mine['id']}/confirm")
    assert r.status_code == 200 and r.json()["url"].startswith("https://t.me/AvaNewBot?start=")
    code = code_in(r.json()["url"])
    confirmed = new.handle(tgm(ME, f"/start {code}", NEW))
    assert "Confirmed" in (confirmed.reply or "")
    assert new.handle(tgm(ME, "hello", NEW)).mode == "known"
    assert identity().endpoint_key == NEW
    assert audits("endpoint_confirmed")
    assert app.get("/app/channels/linked").json()["accounts"][0]["pending"] is None
    # The link is bound to one bot: going back to the old one needs confirming again.
    assert old.handle(tgm(ME, "hello", OLD)).mode == "unconfirmed"


def test_confirm_codes_from_someone_else_expired_or_for_another_bot_are_refused(bots):
    old, new = bots
    app = owner_app(new)
    link_on(old, OLD)
    new.handle(tgm(ME, "hi", NEW))
    me_id = identity().id
    url = app.post(f"/app/channels/linked/{me_id}/confirm").json()["url"]
    code = code_in(url)

    # Someone else opening the owner's confirm link gets nothing.
    other = new.handle(tgm("5550999", f"/start {code}", NEW))
    assert "expired or was already used" in (other.reply or "")
    assert identity("5550999").person_id is None
    assert any(d.get("reason") == "other_account" for d in audits("invite_rejected"))

    # Through a different bot (one the code wasn't made for), refused.
    third = Agent(llm=new.llm, messengers=new.messengers, settings=new.settings)
    third_msg = tgm(ME, f"/start {code}", THIRD)
    assert "expired or was already used" in (third.handle(third_msg).reply or "")
    assert any(d.get("reason") == "wrong_endpoint" for d in audits("invite_rejected"))

    # Expired: refused.
    with session_scope() as s:
        for invite in s.scalars(select(ChannelInvite)).all():
            invite.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert "expired" in (new.handle(tgm(ME, f"/start {code}", NEW)).reply or "")
    assert new.handle(tgm(ME, "still me?", NEW)).mode == "unconfirmed"

    # A fresh link from the app works, once.
    code = code_in(app.post(f"/app/channels/linked/{me_id}/confirm").json()["url"])
    assert "Confirmed" in (new.handle(tgm(ME, f"/start {code}", NEW)).reply or "")
    assert new.handle(tgm(ME, "hello", NEW)).mode == "known"


def test_a_first_link_through_another_bot_is_refused(bots):
    old, new = bots
    owner_app(new)
    with session_scope() as s:
        owner = s.scalar(select(Person).where(Person.relation == "manager"))
        code = accounts.create_invite(s, owner, "telegram", endpoint=OLD)
    assert "expired or was already used" in (new.handle(tgm(ME, f"/start {code}", NEW)).reply or "")
    assert identity().person_id is None


def test_no_confirm_link_without_a_pending_account_or_a_session(bots, browser_for_new):
    old, new = bots
    app = owner_app(new)
    link_on(new, NEW)
    me_id = identity().id
    assert app.post(f"/app/channels/linked/{me_id}/confirm").status_code == 409  # nothing to confirm
    stranger = browser_for_new()
    assert stranger.get("/app/channels/linked").status_code == 401
    assert stranger.post(f"/app/channels/linked/{me_id}/confirm").status_code == 401
    assert stranger.post(f"/app/channels/linked/{me_id}/unlink").status_code == 401


@pytest.fixture
def browser_for_new(bots):
    _, new = bots
    app = create_app(new)
    return lambda: TestClient(app, headers={"X-Requested-With": "superaide"})


def test_unlink_in_connections_takes_effect_at_once(bots):
    _, new = bots
    app = owner_app(new)
    link_on(new, NEW)
    assert new.handle(tgm(ME, "hello", NEW)).mode == "known"
    row = app.get("/app/channels/linked").json()["accounts"][0]
    assert row["endpoint"] == "@AvaNewBot" and row["pending"] is None
    assert app.post(f"/app/channels/linked/{row['id']}/unlink").json() == {"unlinked": True}
    assert new.handle(tgm(ME, "hello", NEW)).mode == "stranger"
    assert app.get("/app/channels/linked").json()["accounts"] == []
    assert app.get("/app/me").json()["channels"]["telegram"] is False


def test_a_long_silence_needs_confirming_again(bots, model):
    _, new = bots
    app = owner_app(new)
    link_on(new, NEW)
    with session_scope() as s:
        row = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == ME))
        row.last_seen_at = datetime.now(UTC) - timedelta(days=31)
    calls = len(model.systems)
    result = new.handle(tgm(ME, "hi, it's me", NEW))
    assert result.mode == "unconfirmed" and result.reply and result.reply.startswith("It's been a while.")
    assert len(model.systems) == calls
    # Still unconfirmed on the next message (seeing it again doesn't reset the clock).
    assert new.handle(tgm(ME, "hello?", NEW)).mode == "unconfirmed"
    assert len(security_notices()) == 1
    code = code_in(app.post(f"/app/channels/linked/{identity().id}/confirm").json()["url"])
    assert "Confirmed" in (new.handle(tgm(ME, f"/start {code}", NEW)).reply or "")
    assert new.handle(tgm(ME, "hello", NEW)).mode == "known"


def test_in_a_group_an_unconfirmed_owner_gets_no_reply(bots, model):
    old, new = bots
    owner_app(new)
    link_on(old, OLD)
    result = new.handle(tgm(ME, "@AvaNewBot what's my schedule?", NEW, chat_kind="group"))
    assert result.mode == "unconfirmed" and result.reply is None
    assert len(security_notices()) == 1


def test_reminders_are_not_pushed_through_an_unconfirmed_bot(bots, monkeypatch):
    from superaide.proactive import notify
    from superaide.vault import store

    old, new = bots
    owner_app(new)
    link_on(old, OLD)
    sent: list[str] = []
    monkeypatch.setattr(store, "runtime_settings", lambda base=None: new.settings)
    monkeypatch.setattr(
        notify, "messenger_for", lambda provider: type("M", (), {"send_dm": lambda self, i, t: sent.append(t)})()
    )
    assert notify.send_via("telegram", identity(), "Your 3pm with the bank") is False
    assert sent == []
    monkeypatch.setattr(store, "runtime_settings", lambda base=None: old.settings)
    assert notify.send_via("telegram", identity(), "Your 3pm with the bank") is True


def test_link_codes_are_not_sent_to_an_account_on_an_unconfirmed_bot(bots, outbox):
    """Whoever holds the owner's Telegram can't use it to link a new Slack account."""
    old, new = bots
    owner_app(new)
    link_on(old, OLD)
    from tests.conftest import sl

    new.handle(sl("U_IMPOSTOR", "Hi, I'm Jordan"))
    assert not [m for m in outbox["telegram"].sent if re.search(r"\b\d{6}\b", m["text"])]
    tg_id = str(identity().id)
    assert all(d.get("via_identity_id") != tg_id for d in audits("link_challenge_sent"))


# --- sensitive actions only in the app -----------------------------------------------


SENSITIVE = [
    "approve_action",
    "change_connection",
    "change_credentials",
    "export_data",
    "delete_data",
    "change_trust",
    "change_settings",
    "change_identity",
]


def test_sensitive_changes_are_refused_from_telegram_even_for_the_owner(bots, model):
    _, new = bots
    app = owner_app(new)
    link_on(new, NEW)
    for kind in SENSITIVE:
        model.next_action = RequestedAction.model_construct(kind=kind, summary=f"please {kind}")
        new.handle(tgm(ME, f"please {kind}", NEW))
    model.next_action = RequestedAction(kind="none", summary="")
    refused = {d["kind"] for d in audits("action_refused")}
    assert set(SENSITIVE) <= refused
    with session_scope() as s:
        assert s.scalars(select(Approval)).all() == []
    assert app.get("/app/connections").status_code == 200  # the app is where they happen


def test_an_approval_cannot_be_decided_from_telegram(bots, model):
    _, new = bots
    app = owner_app(new)
    link_on(new, NEW)
    model.next_action = RequestedAction(kind="publish_post", summary="Publish the launch post")
    new.handle(tgm(ME, "Publish the launch post", NEW))
    model.next_action = RequestedAction(kind="none", summary="")
    approval = app.get("/app/approvals").json()[0]
    for said in (f"approve {approval['id']}", "/approve", "yes, approve it", f"/start approve_{approval['id'][:8]}"):
        new.handle(tgm(ME, said, NEW))
    assert app.get("/app/approvals").json()[0]["status"] == "pending"


def test_the_owner_turn_on_telegram_offers_no_sensitive_tools(bots, model, monkeypatch):
    _, new = bots
    owner_app(new)
    link_on(new, NEW)
    seen: list[str] = []
    step = new.llm.step

    def recording(**kw):
        seen.extend(t.get("name", "") for t in kw["tools"])
        return step(**kw)

    monkeypatch.setattr(new.llm, "step", recording)
    assert new.handle(tgm(ME, "what can you do?", NEW)).mode == "known"
    allowed = {
        "propose_action", "recall", "create_reminder", "list_reminders", "update_reminder", "cancel_reminder",
        "calendar_events", "web_search", "web_fetch",
    }  # fmt: skip
    assert seen and set(seen) <= allowed


# --- channels and the migration ------------------------------------------------------


def test_adapters_record_which_endpoint_received_the_message():
    assert tgm(ME, "hi", NEW).endpoint == NEW
    payload = {
        "team_id": "T_OURS",
        "api_app_id": "A_AVA",
        "event": {"type": "message", "channel_type": "im", "user": "U1", "text": "hi", "ts": "1.0", "channel": "D1"},
    }
    msg = parse_event(payload, our_team_id="T_OURS", verified=True)
    assert msg is not None and msg.endpoint == "T_OURS/A_AVA"


def test_a_link_from_before_binding_binds_to_the_bot_it_next_writes_through(bots, manager):
    """The conftest manager was added like add-manager does: no bot yet."""
    old, _ = bots
    assert identity("1001").endpoint_key == ""
    assert old.handle(tgm("1001", "hi", OLD)).mode == "known"
    assert identity("1001").endpoint_key == OLD
    assert audits("endpoint_bound")


def test_the_migration_backfill_binds_existing_links_to_the_current_bot(manager):
    with session_scope() as s:
        s.add(ChannelIdentity(provider="telegram", stable_key="777", aux_handles={}))  # not linked: left alone
    with get_engine().begin() as conn:
        assert bind_unbound_telegram(conn, OLD) == 1
        assert bind_unbound_telegram(conn, NEW) == 0  # already bound: never moved by a later run
        assert bind_unbound_telegram(conn, "") == 0
        rows = dict(
            conn.execute(text("SELECT stable_key, endpoint_key FROM channel_identity WHERE provider='telegram'")).all()
        )
    assert rows == {"1001": OLD, "777": ""}


def test_confirm_and_unlink_only_touch_the_owners_own_chat_accounts(bots, colleague):
    _, new = bots
    app = owner_app(new)
    link_on(new, NEW)
    with session_scope() as s:
        owner = s.scalar(select(Person).where(Person.relation == "manager"))
        web = s.scalar(select(ChannelIdentity).where(ChannelIdentity.provider == "web"))
        if web is None:  # the app's own account for the owner
            web = ChannelIdentity(provider="web", stable_key=str(owner.id), person_id=owner.id, aux_handles={})
            s.add(web)
            s.flush()
        priya = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == "U_PRIYA"))
        priya.verification = {"pending": {"endpoint": "T_OURS/A_NEW", "reason": "new_endpoint"}}
        web_id, priya_id = web.id, priya.id
    for other in (web_id, priya_id):
        assert app.post(f"/app/channels/linked/{other}/confirm").status_code == 404
        assert app.post(f"/app/channels/linked/{other}/unlink").status_code == 404
    with session_scope() as s:
        assert s.get(ChannelIdentity, web_id).person_id is not None
        assert s.get(ChannelIdentity, priya_id).person_id is not None
    rows = app.get("/app/channels/linked").json()["accounts"]
    assert [r["account"] for r in rows] == ["•••001"]  # someone else's account isn't listed


def test_an_unconfirmed_bot_cannot_refresh_the_silence_clock(bots):
    old, new = bots
    app = owner_app(new)
    link_on(old, OLD)
    long_ago = datetime.now(UTC) - timedelta(days=31)
    with session_scope() as s:
        s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == ME)).last_seen_at = long_ago
    for _ in range(3):
        assert new.handle(tgm(ME, "keep me fresh", NEW)).mode == "unconfirmed"
    row = identity()
    assert row.last_seen_at == long_ago
    assert app.get("/app/channels/linked").json()["accounts"][0]["last_seen_unconfirmed_at"]
    # Back on the old bot the silence still counts.
    assert old.handle(tgm(ME, "hi", OLD)).mode == "unconfirmed"
