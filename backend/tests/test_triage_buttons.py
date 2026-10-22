"""ADR 0010 TR4: Acknowledge / Snooze / Not urgent buttons on Telegram escalation pushes.

Only the instance owner's own verified Telegram account may press; a repeated press is a no-op;
every press is answered; and once answered, every Telegram copy loses its buttons.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import func, select

from superaide.channels import telegram
from superaide.channels.base import InboundAction
from superaide.db.models import AuditLog, ChannelIdentity, Job
from superaide.db.session import session_scope
from superaide.triage import buttons, escalate
from superaide.triage.models import EscalationAck, EscalationDelivery
from tests import test_triage_escalation as base
from tests.conftest import COLLEAGUE_SLACK, sl

# The world, the flag-on agent and the fake pushes from the TR3 tests.
world, on, pushes = base.world, base.on, base.pushes
drain, make_vip, the_escalation = base.drain, base.make_vip, base.the_escalation

OWNER_TG = "9001"


def press_of(outbox, *, by: str = OWNER_TG, verb: str = "a", press_id: str = "cb-1", bot: str = "") -> InboundAction:
    sent = next(m for m in outbox["telegram"].sent if m.get("buttons"))
    data = next(d for _, d in sent["buttons"] if d.endswith(f":{verb}"))
    chat, _, message = sent["ref"].partition(":")
    return InboundAction(provider="telegram", sender_id=by, action_id=press_id, data=data, chat_id=chat,
                         message_id=message, endpoint=bot, verified=True)  # fmt: skip


@pytest.fixture
def escalated(world, on, pushes):
    make_vip(world)
    on.handle(sl(COLLEAGUE_SLACK, "The server is down"))
    drain()
    return pushes


def test_the_telegram_push_carries_three_small_buttons(escalated):
    sent = [m for m in escalated["telegram"].sent if m.get("buttons")]
    assert len(sent) == 1
    labels = [label for label, _ in sent[0]["buttons"]]
    assert labels == ["Acknowledge", "Snooze 30 min", "Not urgent"]
    for _, data in sent[0]["buttons"]:
        assert len(data.encode()) <= 64 and data.startswith("e1:")
        assert str(the_escalation().id) not in data  # an opaque token, never the escalation id
    with session_scope() as s:
        row = s.scalar(select(EscalationDelivery).where(EscalationDelivery.provider == "telegram"))
        assert row is not None and row.external_ref == sent[0]["ref"] and row.token in sent[0]["buttons"][0][1]


def test_the_owner_acknowledges_and_the_message_is_edited(escalated):
    reply = buttons.on_telegram_press("TOKEN", press_of(escalated))
    assert reply == "Acknowledged"
    assert escalated["telegram"].answers == [{"callback_id": "cb-1", "text": "Acknowledged"}]
    esc = the_escalation()
    assert esc.state == "acked" and esc.acked_via == "telegram"
    drain()  # the refresh job edits every Telegram copy
    assert escalated["telegram"].edits and "Acknowledged via Telegram" in escalated["telegram"].edits[0]["text"]
    with session_scope() as s:
        step2 = s.scalar(select(Job).where(Job.dedupe_key == escalate.step_key(esc.id, 2)))
        assert step2 is not None and step2.status == "cancelled"


def test_the_same_press_twice_is_one_ack(escalated):
    p = press_of(escalated)
    buttons.on_telegram_press("TOKEN", p)
    assert buttons.on_telegram_press("TOKEN", p) == "Already handled."
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(EscalationAck)) == 1
    assert len(escalated["telegram"].answers) == 2  # answered every time


def test_someone_else_pressing_is_refused_and_audited(escalated, world):
    with session_scope() as s:  # Priya on Telegram, e.g. the push was forwarded to a group
        s.add(ChannelIdentity(person_id=world["colleague_id"], provider="telegram", tenant_scope="", stable_key="7777",
                              trust_level=2))  # fmt: skip
    reply = buttons.on_telegram_press("TOKEN", press_of(escalated, by="7777"))
    assert reply.startswith("Only the person")
    assert the_escalation().state == "notifying"
    with session_scope() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "escalation_ack_refused")) is not None
    assert escalated["telegram"].answers[-1]["text"].startswith("Only the person")


def test_the_owner_on_a_bot_they_did_not_confirm_is_refused(escalated):
    with session_scope() as s:
        ident = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == OWNER_TG))
        ident.endpoint_key = "111"  # confirmed on another bot
    assert buttons.on_telegram_press("TOKEN", press_of(escalated, bot="222")).startswith("Only the person")
    assert the_escalation().state == "notifying"


@pytest.mark.parametrize("data", ["e1:nope:a", "garbage", "e1:abcdefghijkl:x", ""])
def test_unknown_or_malformed_buttons_are_answered_and_ignored(escalated, data):
    p = press_of(escalated)
    p.data = data
    assert buttons.on_telegram_press("TOKEN", p) == "That button has expired."
    assert the_escalation().state == "notifying"


def test_snooze_and_not_urgent_from_the_button(escalated):
    assert buttons.on_telegram_press("TOKEN", press_of(escalated, verb="s")) == "Snoozed for 30 minutes"
    assert the_escalation().state == "deferred"


def test_not_urgent_from_the_button_records_feedback(escalated):
    from superaide.triage.models import TriageDecision

    assert buttons.on_telegram_press("TOKEN", press_of(escalated, verb="n")) == "Marked not urgent"
    with session_scope() as s:
        assert s.scalar(select(TriageDecision.feedback)) == "not_urgent"


def test_an_ack_in_the_app_also_removes_the_telegram_buttons(escalated, world):
    from superaide.db.models import Person

    with session_scope() as s:
        escalate.ack(s, the_escalation().id, source="app", external_id="req-1", verb="ack",
                     actor=s.get(Person, world["owner_id"]))  # fmt: skip
    drain()
    assert "Acknowledged via the app" in escalated["telegram"].edits[0]["text"]


# --- the wire ----------------------------------------------------------------------------


def callback_update(sender: str = OWNER_TG, data: str = "e1:abcdefghijkl:a") -> dict:
    return {
        "update_id": 5,
        "callback_query": {
            "id": "4382bfdwdsb323b2d9", "from": {"id": int(sender), "is_bot": False, "first_name": "S"},
            "message": {"message_id": 42, "chat": {"id": int(sender), "type": "private"}}, "data": data,
        },
    }  # fmt: skip


def test_parse_callback_reads_the_press_and_ignores_the_rest():
    a = telegram.parse_callback(callback_update(), verified=True, bot="123")
    assert a is not None and (a.sender_id, a.action_id, a.chat_id, a.message_id, a.endpoint) == (
        OWNER_TG, "4382bfdwdsb323b2d9", OWNER_TG, "42", "123")  # fmt: skip
    bot = callback_update()
    bot["callback_query"]["from"]["is_bot"] = True
    assert telegram.parse_callback(bot, verified=True) is None
    assert telegram.parse_callback({"message": {"text": "hi"}}, verified=True) is None
    assert telegram.parse_update(callback_update(), verified=True) is None  # not a message


def test_the_webhook_routes_a_press(escalated, agent):
    from fastapi.testclient import TestClient

    from superaide.main import create_app

    p = press_of(escalated)
    c = TestClient(create_app(agent))
    update = callback_update(data=p.data)
    bad = c.post("/webhooks/telegram", json=update, headers={telegram.SECRET_HEADER: "wrong"})
    assert bad.status_code == 401 and the_escalation().state == "notifying"
    r = c.post("/webhooks/telegram", json=update, headers={telegram.SECRET_HEADER: "test-telegram-secret"})
    assert r.status_code == 200
    assert the_escalation().state == "acked"


def test_polling_hands_presses_to_triage(monkeypatch):
    got: list[InboundAction] = []
    monkeypatch.setattr(telegram, "_handle_press", lambda token, action: got.append(action))
    stop = __import__("threading").Event()
    real_client = httpx.Client
    requests: list[httpx.Request] = []

    def fake(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        stop.set()
        return httpx.Response(200, json={"ok": True, "result": [callback_update()]})

    monkeypatch.setattr(telegram.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(fake)))
    telegram.poll_forever("123:abc", lambda m: None, stop)
    assert len(got) == 1 and got[0].endpoint == "123" and got[0].verified
    assert json.loads(requests[-1].url.params["allowed_updates"]) == ["message", "callback_query"]


def test_the_real_messenger_sends_buttons_answers_and_edits():
    calls: list[tuple[str, dict]] = []

    def fake(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path.rsplit("/", 1)[1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    m = telegram.TelegramMessenger("123:abc", client=httpx.Client(transport=httpx.MockTransport(fake)))
    ref = m.send_buttons("9001", "Needs you: **Dana**: outage", buttons.keyboard("abcdefghijkl"))
    assert ref == "9001:77"
    assert m.answer_callback("cb", "Acknowledged") and m.edit_message(ref, "done")
    (send, body), (answer, a), (edit, e) = calls
    assert (
        send == "sendMessage" and body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "e1:abcdefghijkl:a"
    )
    assert "**" not in body["text"]  # plain text, no markup to break
    assert answer == "answerCallbackQuery" and a["callback_query_id"] == "cb"
    assert edit == "editMessageText" and e["message_id"] == 77 and "reply_markup" not in e
