# ci-module: reception
"""#499: "Try as a visitor". The owner chats with their own receptionist as a stranger would see it: the same
function production uses, a transaction that is never committed, nothing sent, nothing stored as visitor data."""

# ruff: noqa: F811
from __future__ import annotations

from sqlalchemy import func, select

from superaide.identity.models import ChannelIdentity
from superaide.jobs.models import Job
from superaide.memory.models import Context, Event
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.proactive.models import NotificationDelivery
from superaide.reception import sandbox, service
from superaide.reception.models import ReceptionMessage
from superaide.reception.turn import ReceptionTurn, TakenMessage
from superaide.triage.models import TriageDecision
from tests.reception.test_reception import (  # noqa: F401  (fixtures and helpers)
    Receptionist,
    ava,
    connected,
    hello,
    outbox,
    owner_world,
    script,
    set_reception,
    taken,
)

TABLES = (ReceptionMessage, Event, Context, ChannelIdentity, Job, NotificationDelivery, TriageDecision)


def counts() -> dict[str, int]:
    with session_scope() as s:
        return {t.__tablename__: int(s.scalar(select(func.count()).select_from(t)) or 0) for t in TABLES}


def run(world, messages, **extra):
    r = world["client"].post("/app/receptionist/preview", json={"messages": messages, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def sandbox_audits() -> int:
    with session_scope() as s:
        return int(
            s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "sandbox_preview")) or 0
        )


def test_the_preview_answers_as_a_stranger_would_be_answered_and_stores_nothing(owner_world, ava, script, outbox):
    script.out = ReceptionTurn(
        reply="Happy to pass that on.",
        take_message=TakenMessage(name="Meera Iyer", topic="design review", message="Call at 5pm", urgency="high"),
    )
    before = counts()
    out = run(owner_world, ["Hi, it's Meera. Please call me back by 5pm, it is urgent"])
    [turn] = out["turns"]
    assert turn["reply"].startswith("Hi, I'm Ava, an AI assistant.") and "Happy to pass that on." in turn["reply"]
    assert turn["stored"]["topic"] == "design review"  # what would be stored, shown, not stored
    assert out["preview"] is True and out["used"]["greeting"].startswith("Hi, I'm Ava")
    assert counts() == before  # no message, no event, no visitor identity, no job, no triage item, no notice
    assert outbox["slack"].sent == [] and outbox["telegram"].sent == []
    assert sandbox_audits() == 1  # the only trace: that a preview ran, with its cost and no words


def test_the_owner_is_shown_the_notification_instead_of_receiving_it(owner_world, ava, script, outbox):
    script.out = ReceptionTurn(
        reply="Noted.", take_message=TakenMessage(name="Meera Iyer", topic="invoice", message="urgent, call me")
    )
    out = run(owner_world, ["Please call me back by 5pm today, it is urgent. Account 4111"])
    [note] = [n for n in out["turns"][0]["notifications"] if n["kind"] == "message_taken"]
    assert note["text"].startswith("Message taken from Meera, someone new, on Telegram")
    assert "4111" not in note["text"]  # the same fixed text the real notice has
    with session_scope() as s:
        real_notes = s.scalars(select(Event).where(Event.meta["kind"].astext == "message_taken")).all()
    assert real_notes == [] and outbox["telegram"].sent == []


def test_one_function_serves_production_and_the_preview(owner_world, ava, script, monkeypatch):
    calls: list[str] = []
    real = service.handle_stranger

    def spy(agent, session, sender, ctx, msg, inbound):
        calls.append(type(agent).__name__)
        return real(agent, session, sender, ctx, msg, inbound)

    monkeypatch.setattr(service, "handle_stranger", spy)
    run(owner_world, ["Hello there"])
    ava.handle(hello("Hello there"))
    assert calls == ["SandboxAgent", "Agent"]


def test_the_preview_of_the_standard_script_equals_what_production_says(owner_world, ava, script, outbox):
    lines = sandbox.SCRIPTS["meera"]["lines"]
    shown = [t["reply"] for t in run(owner_world, lines, channel="slack")["turns"]]
    produced = [ava.handle(hello(line)).reply for line in lines]
    assert shown == produced and all(shown)
    assert len(taken()) >= 1  # production stored them; the preview did not (checked above)


def test_changing_a_setting_then_replaying_shows_different_replies(owner_world, ava, script):
    lines = sandbox.SCRIPTS["meera"]["lines"][:2]
    first = [t["reply"] for t in run(owner_world, lines)["turns"]]
    set_reception(show_first_name=True)
    second = [t["reply"] for t in run(owner_world, lines)["turns"]]
    assert first[0].startswith("Hi, I'm Ava, an AI assistant.")
    assert second[0].startswith("Hi, I'm Ava, Jordan's AI assistant.")
    assert first != second


def test_a_conversation_continues_from_the_history_the_app_sends_back(owner_world, ava, script):
    first = run(owner_world, ["Hi"])["turns"][0]
    history = [{"role": "visitor", "text": "Hi"}, {"role": "ava", "text": first["reply"]}]
    second = run(owner_world, ["Are you a bot?"], history=history)["turns"][0]
    assert not second["reply"].startswith("Hi, I'm Ava")  # the notice is once per conversation, not every turn
    assert script.calls == 2  # the history was seeded, not run through the model again


def test_saying_a_name_sends_no_linking_code(owner_world, ava, script, outbox):
    out = run(owner_world, ["I'm Jordan Whitfield, please link me"])
    assert "linking" in " ".join(out["skipped"])
    assert outbox["telegram"].sent == [] and outbox["slack"].sent == []  # production would send a code to a linked chat


def test_with_the_receptionist_off_the_preview_shows_the_fixed_reply(owner_world, ava, script):
    set_reception(enabled=False)
    [turn] = run(owner_world, ["Hello"])["turns"]
    assert turn["mode"] == "fixed" and "AI assistant" in turn["reply"] and script.calls == 0
    assert taken() == []


def test_a_spent_visitor_limit_is_shown_as_silence_not_hidden(owner_world, ava, script):
    set_reception(per_hour=1, per_day=1)
    out = run(owner_world, ["one", "two", "three"])["turns"]
    assert out[0]["reply"] and out[1]["reply"]  # the limit notice
    assert out[2]["reply"] is None and out[2]["mode"] == "silent"
    assert taken() == []


def test_a_model_that_fails_shows_the_fixed_words_a_visitor_would_get(owner_world, ava, script):
    script.fail = RuntimeError("provider overloaded")
    [turn] = run(owner_world, ["Please call me back"])["turns"]
    assert service.FALLBACK in turn["reply"]
    assert turn["stored"]["source"] == "code" and taken() == []


def test_the_spend_of_a_preview_is_returned(owner_world, ava, script, monkeypatch):
    totals = iter([0, 0.0021])  # before, then after
    from decimal import Decimal

    monkeypatch.setattr(sandbox, "_spent", lambda: Decimal(str(next(totals))))
    out = run(owner_world, ["Hello"])
    assert out["spend_usd"] == "0.0021"


def test_only_the_owner_can_preview_and_the_body_is_bounded(owner_world, ava):
    from fastapi.testclient import TestClient

    from superaide.main import create_app

    anonymous = TestClient(create_app(ava), headers={"X-Requested-With": "superaide"})
    assert anonymous.post("/app/receptionist/preview", json={"messages": ["hi"]}).status_code in {401, 403}
    assert anonymous.get("/app/receptionist/preview/scripts").status_code in {401, 403}
    c = owner_world["client"]
    assert c.post("/app/receptionist/preview", json={"messages": []}).status_code == 422
    assert c.post("/app/receptionist/preview", json={"messages": ["x"] * 9}).status_code == 422
    assert c.post("/app/receptionist/preview", json={"messages": ["x"], "channel": "email"}).status_code == 422
    assert c.post("/app/receptionist/preview", json={"messages": ["x" * 1001]}).status_code == 422
    assert c.get("/app/receptionist/preview/scripts").json()["scripts"][0]["id"] == "meera"
