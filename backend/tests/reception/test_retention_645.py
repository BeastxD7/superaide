# ci-module: reception
"""#645: retention keeps everything. A visitor message of any age is readable by the owner (with its date); only
"waiting" has a recency rule; relaying an old message needs the owner's say-so; and old messages stay invisible to
everyone but the owner."""

# ruff: noqa: F811
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from superaide.approvals.models import Approval
from superaide.evals.checks import _audience
from superaide.memory.audience import Audience
from superaide.memory.models import Event
from superaide.memory.recall.retrieval import recall_scoped, visitor_rows
from superaide.platform.db.session import session_scope
from superaide.proactive import notify
from superaide.reception.inbox import awareness_line
from superaide.reception.models import ReceptionMessage
from tests.conftest import sl
from tests.reception.test_inbox import OWNER, Owner, ask, model, write  # noqa: F401
from tests.reception.test_reception import (  # noqa: F401  (fixtures and helpers)
    ava,
    connected,
    outbox,
    owner_world,
    script,
)

ASKED = "Please call me about the quarterly invoice"


@pytest.fixture(autouse=True)
def wired(monkeypatch, outbox) -> None:
    monkeypatch.setattr(notify, "messenger_for", lambda provider: outbox.get(provider))


def age(days: int) -> None:
    """Make every stored visitor message `days` old."""
    with session_scope() as s:
        s.execute(update(ReceptionMessage).values(created_at=datetime.now(UTC) - timedelta(days=days)))


def relay(ava, model, args: dict[str, Any], text: str = "tell Meera I'm busy, I'll reply in an hour") -> str:
    model.tool = {"name": "relay_message", "input": args}
    model.step = 0
    ava.handle(sl(OWNER, text))
    return model.calls[-1].get("tool_result", "")


def cards() -> list[Approval]:
    with session_scope() as s:
        rows = list(s.scalars(select(Approval).where(Approval.kind == "relay_message")))
        s.expunge_all()
        return rows


@pytest.mark.parametrize("days", [31, 400])
def test_an_old_visitor_message_is_in_the_owners_inbox_with_its_date_but_not_waiting(
    owner_world, ava, script, model, days
):
    write(ava, script, ASKED)
    age(days)
    shown = ask(ava, model, {"status": "all"})
    year = (datetime.now(UTC) - timedelta(days=days)).year
    assert "1 conversation" in shown and "Meera" in shown and str(year) in shown  # read, with its date
    opened = ask(ava, model)  # still open: listed as unanswered since a date, never "waiting"
    assert "unanswered since" in opened and "waiting " not in opened.split("since")[0].split("·")[-1]
    with session_scope() as s:
        assert awareness_line(s) == ""  # the prompt's "People waiting" line has a recency rule
    row_id = shown.split("- id ")[1].split(" ")[0]
    assert ASKED in ask(ava, model, {"id": row_id}, "what did she say?")  # the words are readable too


def test_a_recent_open_message_is_still_waiting_and_in_the_prompt_line(owner_world, ava, script, model):
    write(ava, script, ASKED)
    age(26)
    with session_scope() as s:
        assert "Meera on Slack, waiting" in awareness_line(s)
    assert "waiting 26 days" in ask(ava, model)


@pytest.mark.parametrize("days", [31, 400])
def test_the_owners_recall_returns_old_visitor_messages_with_their_date(owner_world, ava, script, model, days):
    write(ava, script, ASKED)
    age(days)
    with session_scope() as s:
        got = recall_scoped(s, _audience("owner"), "invoice", rooms=True, asked="what did Meera say about the invoice?")
    [src] = [x for x in got.sources if "Someone Ava does not know" in x.text]
    assert (datetime.now(UTC) - timedelta(days=days)).strftime("%Y") in src.title and ASKED in src.text


@pytest.mark.parametrize("who", ["colleague", "stranger", "two people", "another owner"])
def test_old_visitor_messages_stay_invisible_to_everyone_but_the_owner(owner_world, ava, script, model, who):
    write(ava, script, ASKED)
    age(400)
    aud = {
        "colleague": Audience({uuid.uuid4()}, False, 2, True),
        "stranger": Audience(set(), True, 0, False),
        "two people": Audience({uuid.uuid4(), uuid.uuid4()}, False, 3, True),
        "another owner": Audience({uuid.uuid4()}, False, 3, True),
    }[who]
    with session_scope() as s:
        got = recall_scoped(s, aud, "invoice", rooms=True, asked="invoice")
        assert not [x for x in got.sources if "Someone Ava does not know" in x.text]
        assert visitor_rows(s, aud) == []


def test_relaying_to_a_sender_whose_last_message_is_old_names_the_age_and_needs_the_owners_say_so(
    owner_world, ava, script, model, outbox
):
    write(ava, script, "can you ask him to call me?")
    age(200)
    refused = relay(ava, model, {"to": "Meera", "text": "The owner is busy."})
    assert "200 days old" in refused and "exists" in refused and "confirm_old" in refused
    assert cards() == []  # nothing queued, nothing sent
    queued = relay(ava, model, {"to": "Meera", "text": "The owner is busy.", "confirm_old": True})
    assert "Queued for the owner's approval" in queued
    [card] = cards()
    assert card.status == "pending" and "Meera" in card.payload["summary"]  # the normal card path
    assert not [m for m in outbox["slack"].sent if "this is Ava" in m["text"]]  # nothing sent before approval


def test_a_recent_message_needs_no_extra_confirmation(owner_world, ava, script, model):
    write(ava, script, "can you ask him to call me?")
    age(29)
    assert "Queued for the owner's approval" in relay(ava, model, {"to": "Meera", "text": "The owner is busy."})


def test_a_model_that_says_confirm_old_without_a_boolean_is_not_believed(owner_world, ava, script, model):
    write(ava, script, "can you ask him to call me?")
    age(200)
    out = relay(ava, model, {"to": "Meera", "text": "The owner is busy.", "confirm_old": "true"})
    assert "days old" in out and cards() == []


def test_erased_room_messages_are_not_recalled_by_the_owner(agent, owner_world):
    from superaide.memory.models import Context  # noqa: F401

    with session_scope() as s:
        ctx = Context(provider="slack", tenant_scope="T", external_id="C1", kind="group", audience_class="internal")
        s.add(ctx)
        s.flush()
        s.add(Event(context_id=ctx.id, direction="in", content="zebra budget talk", meta={"ambient": True}))
        s.flush()
        owner = _audience("owner")
        assert recall_scoped(s, owner, "zebra budget", rooms=True, asked="zebra budget").sources
        s.execute(update(Event).where(Event.content == "zebra budget talk").values(erased_at=datetime.now(UTC)))
        assert not recall_scoped(s, owner, "zebra budget", rooms=True, asked="zebra budget").sources
