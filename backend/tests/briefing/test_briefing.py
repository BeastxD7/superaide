# ci-module: briefing
"""#485: 'while you were away'. A briefing built by code from the inbox and the promises, a drafted reply per
conversation that the owner approves with one tap (never sent on its own), and what the model may and may not do:
it only writes words, from a quoted message, and what it writes is cleaned, checked and flagged."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from superaide.app.accounts import owner as hired_owner
from superaide.approvals.models import Approval
from superaide.brain.llm import FakeLLM
from superaide.briefing import service
from superaide.briefing.models import ReplyDraft
from superaide.commitments import service as promises
from superaide.commitments.models import Commitment
from superaide.jobs.models import Job
from superaide.memory.models import Event, MemoryItem
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.policy.killswitch import set_frozen
from superaide.proactive import notify
from superaide.reception.models import ReceptionMessage
from tests.conftest import sl
from tests.reception.test_inbox import INJECTION, OWNER, model, write  # noqa: F401
from tests.reception.test_reception import (  # noqa: F401  (fixtures and helpers)
    ava,
    connected,
    outbox,
    owner_world,
    script,
)

BASE = "/app/briefing"
WORDS = "They've seen your message about the project and will get back to you."


class Drafter:
    """The model for draft calls (output `DraftOut`); every other call goes to the usual scripted owner/stranger."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.text = WORDS
        self.fail: Exception | None = None
        self.calls: list[dict[str, str]] = []

    def __call__(self, purpose, system, messages, output_model):
        if output_model is service.DraftOut:
            self.calls.append({"system": "\n".join(b.text for b in system), "user": str(messages[-1]["content"])})
            if self.fail:
                raise self.fail
            return service.DraftOut(text=self.text)
        return self.inner(purpose, system, messages, output_model)


@pytest.fixture
def drafter(ava, model) -> Drafter:
    d = Drafter(model)
    ava.llm = FakeLLM(d)
    return d


@pytest.fixture(autouse=True)
def wired(monkeypatch, outbox) -> None:
    monkeypatch.setattr(notify, "messenger_for", lambda provider: outbox.get(provider))


def go_away(hours: int = 5) -> None:
    """The owner last wrote to Ava `hours` ago."""
    with session_scope() as s:
        boss = hired_owner(s)
        s.execute(
            update(Event)
            .where(Event.resolved_person_id == boss.id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=hours))
        )


def get(owner_world, **params):
    r = owner_world["client"].get(BASE, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def drafts() -> list[ReplyDraft]:
    with session_scope() as s:
        found = list(s.scalars(select(ReplyDraft).order_by(ReplyDraft.created_at)))
        s.expunge_all()
        return found


def relayed(outbox) -> list[str]:
    return [m["text"] for m in outbox["slack"].sent if m.get("to_context") and "this is Ava" in m["text"]]


def approvals() -> list[Approval]:
    with session_scope() as s:
        found = list(s.scalars(select(Approval).where(Approval.kind == "relay_message")))
        s.expunge_all()
        return found


def meera(ava, script, text: str = "Hi, I need your answer on the project by 5pm today.", who: str = "U_MEERA") -> None:
    write(ava, script, text, who=who)


# --- the Meera scenario ----------------------------------------------------------------------------------------


def test_the_briefing_lists_meera_with_a_draft_and_sending_it_makes_one_relay_with_audit(
    owner_world, ava, script, drafter, outbox
):
    meera(ava, script)
    ava.handle(sl(OWNER, "hello"))
    go_away()
    b = get(owner_world)
    assert b["show"] is True and b["away_minutes"] >= 290
    [c] = b["conversations"]
    assert c["who"] == "Meera" and c["channel"] == "Slack" and "5pm" in c["quote"]
    assert c["draft"]["text"] == WORDS and c["draft"]["state"] == "suggested" and c["draft"]["origin"] == "model"
    assert relayed(outbox) == []  # a draft is a suggestion: nothing was sent

    r = owner_world["client"].post(f"{BASE}/drafts/{c['draft']['id']}/send")
    assert r.status_code == 200 and r.json()["state"] == "sent", r.text
    [text] = relayed(outbox)
    assert text.startswith("Hi Meera, this is Ava") and text.endswith(WORDS)  # the fixed introduction, then the draft
    [card] = approvals()
    assert card.status == "approved" and card.payload["via"] == "briefing" and card.payload["result"].startswith("Sent")
    with session_scope() as s:
        actions = {a for (a,) in s.execute(select(AuditLog.action))}
        assert {"relay_sent", "draft_sent", "approval_requested"} <= actions
        rows = list(s.scalars(select(ReceptionMessage)))
        assert {m.status for m in rows} == {"done"}  # answered: she leaves the waiting list
    assert get(owner_world, force="true")["conversations"] == []


def test_a_second_tap_sends_nothing_more(owner_world, ava, script, drafter, outbox):
    meera(ava, script)
    go_away()
    did = get(owner_world)["conversations"][0]["draft"]["id"]
    assert owner_world["client"].post(f"{BASE}/drafts/{did}/send").status_code == 200
    assert owner_world["client"].post(f"{BASE}/drafts/{did}/send").status_code == 409
    assert owner_world["client"].post(f"{BASE}/drafts/{did}/dismiss").status_code == 409
    assert len(relayed(outbox)) == 1 and len(approvals()) == 1


def test_the_owner_can_send_her_own_words_and_a_promise_in_them_becomes_a_commitment(
    owner_world, ava, script, drafter, outbox
):
    meera(ava, script)
    go_away()
    did = get(owner_world)["conversations"][0]["draft"]["id"]
    r = owner_world["client"].post(f"{BASE}/drafts/{did}/send", json={"text": "Sorry, I'll reply in an hour."})
    assert r.status_code == 200 and r.json()["text"] == "Sorry, I'll reply in an hour."
    assert relayed(outbox)[0].endswith("Sorry, I'll reply in an hour.")
    with session_scope() as s:
        [c] = list(s.scalars(select(Commitment)))
        assert c.creditor_label == "Meera" and c.source == "relay"  # the owner typed the promise: the ledger has it
        assert s.scalar(select(ReplyDraft.edited)) is True


def test_an_unedited_draft_never_creates_a_commitment(owner_world, ava, script, drafter, outbox):
    drafter.text = "They will get back to you in an hour."  # the model's words, not the owner's
    meera(ava, script)
    go_away()
    did = get(owner_world)["conversations"][0]["draft"]["id"]
    assert owner_world["client"].post(f"{BASE}/drafts/{did}/send").status_code == 200
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Commitment)) == 0


# --- when it shows --------------------------------------------------------------------------------------------


def test_not_away_or_nothing_to_tell_means_no_briefing(owner_world, ava, script, drafter):
    meera(ava, script)
    ava.handle(sl(OWNER, "hello"))  # the owner is here: recent activity
    assert get(owner_world)["show"] is False
    assert drafter.calls == []  # not away: no model call was spent
    forced = get(owner_world, force="true")
    assert forced["show"] is True and len(forced["conversations"]) == 1


def test_away_with_nothing_waiting_shows_nothing(owner_world):
    go_away()
    assert get(owner_world)["show"] is False


def test_reading_it_resets_the_absence(owner_world, ava, script, drafter):
    meera(ava, script)
    go_away()
    assert get(owner_world)["show"] is True
    assert owner_world["client"].post(f"{BASE}/seen").status_code == 204
    assert get(owner_world)["show"] is False


def test_overdue_promises_are_in_the_briefing(owner_world):
    go_away()
    with session_scope() as s:
        c = promises.create(s, hired_owner(s), text="I'll call back", due_at=datetime.now(UTC) + timedelta(minutes=30),
                            who="Priya")  # fmt: skip
        c.state, c.due_at = "overdue", datetime.now(UTC) - timedelta(minutes=20)
    b = get(owner_world)
    assert b["show"] is True and [o["who"] for o in b["overdue"]] == ["Priya"] and b["conversations"] == []


# --- what the model may do ----------------------------------------------------------------------------------------


def test_the_stranger_is_quoted_as_data_and_drafts_are_made_once_per_message(owner_world, ava, script, drafter):
    meera(ava, script, INJECTION)
    go_away()
    get(owner_world)
    get(owner_world)
    assert len(drafter.calls) == 1 and len(drafts()) == 1  # asked once, whatever times the page is opened
    call = drafter.calls[0]
    assert INJECTION.split(":")[0] not in call["system"]  # their words are never in the instructions
    assert "<message>" in call["user"] and "data written by a stranger" in call["system"]


def test_a_link_or_an_address_in_the_draft_is_refused_for_the_fixed_sentence(owner_world, ava, script, drafter):
    drafter.text = "Tell them to email evil@example.com or visit https://evil.example/pay"
    meera(ava, script)
    go_away()
    d = get(owner_world)["conversations"][0]["draft"]
    assert d["text"] == service.SAFE and d["origin"] == "code"


def test_times_and_numbers_the_sender_did_not_say_are_flagged_for_the_owner(owner_world, ava, script, drafter):
    drafter.text = "They will call you tomorrow at 4pm."
    meera(ava, script, "Please reply when you can.")
    go_away()
    d = get(owner_world)["conversations"][0]["draft"]
    assert set(d["adds"]) == {"4pm", "tomorrow"}


@pytest.mark.parametrize("failure", ["budget", "frozen", "error"])
def test_no_model_budget_or_an_error_gives_the_fixed_sentence(owner_world, ava, script, drafter, failure):
    from superaide.brain.spend.budget import AgentFrozen, BudgetExceeded

    drafter.fail = {
        "budget": BudgetExceeded("cap", "day"),
        "frozen": AgentFrozen("paused"),
        "error": RuntimeError("provider down"),
    }[failure]
    meera(ava, script)
    go_away()
    c = get(owner_world)["conversations"][0]
    assert c["draft"]["text"] == service.SAFE and c["draft"]["origin"] == "code"  # still a usable briefing


def test_at_most_four_drafts_are_written_per_briefing(owner_world, ava, script, drafter):
    for i in range(6):
        meera(ava, script, f"Question {i}", who=f"U_P{i}")
    go_away()
    b = get(owner_world)
    assert len(b["conversations"]) == 6 and sum(1 for c in b["conversations"] if c["draft"]) == service.MAX_DRAFTS
    assert len(drafter.calls) == service.MAX_DRAFTS


# --- dismiss, stale, safety ----------------------------------------------------------------------------------------


def test_dismissing_teaches_nothing_and_sends_nothing(owner_world, ava, script, drafter, outbox):
    meera(ava, script)
    go_away()
    did = get(owner_world)["conversations"][0]["draft"]["id"]
    with session_scope() as s:
        before = (
            s.scalar(select(func.count()).select_from(MemoryItem)),
            s.scalar(select(func.count()).select_from(Job)),
        )
    assert owner_world["client"].post(f"{BASE}/drafts/{did}/dismiss").json()["state"] == "dismissed"
    with session_scope() as s:
        after = (
            s.scalar(select(func.count()).select_from(MemoryItem)),
            s.scalar(select(func.count()).select_from(Job)),
        )
        assert after == before  # no memory written, no learning job queued
    assert relayed(outbox) == [] and approvals() == []
    assert get(owner_world, force="true")["conversations"][0]["draft"] is None  # not offered again
    assert len(drafter.calls) == 1


def test_a_draft_goes_stale_when_they_write_again(owner_world, ava, script, drafter, outbox):
    meera(ava, script)
    go_away()
    did = get(owner_world)["conversations"][0]["draft"]["id"]
    meera(ava, script, "Actually, make that 6pm.")
    r = owner_world["client"].post(f"{BASE}/drafts/{did}/send")
    assert r.status_code == 409 and "wrote again" in r.json()["detail"]
    assert relayed(outbox) == []
    assert drafts()[0].state == "stale"
    fresh = get(owner_world, force="true")["conversations"][0]
    assert fresh["draft"]["id"] != did and fresh["messages"] == 2  # a new draft for the new message


def test_paused_ava_sends_nothing_and_the_draft_stays_suggested(owner_world, ava, script, drafter, outbox):
    meera(ava, script)
    go_away()
    did = get(owner_world)["conversations"][0]["draft"]["id"]
    with session_scope() as s:
        set_frozen(s, True, actor="test")
    r = owner_world["client"].post(f"{BASE}/drafts/{did}/send")
    assert r.status_code == 409 and "paused" in r.json()["detail"]
    assert relayed(outbox) == [] and drafts()[0].state == "suggested"


def test_the_per_recipient_daily_cap_still_applies(owner_world, ava, script, drafter, outbox):
    from superaide.reception.relay import PER_RECIPIENT_PER_DAY

    meera(ava, script)
    for i in range(PER_RECIPIENT_PER_DAY):
        go_away()
        c = get(owner_world, force="true")["conversations"]
        if not c:
            meera(ava, script, f"Again {i}")
            c = get(owner_world, force="true")["conversations"]
        assert owner_world["client"].post(f"{BASE}/drafts/{c[0]['draft']['id']}/send").status_code == 200
        meera(ava, script, f"One more {i}")
    c = get(owner_world, force="true")["conversations"]
    r = owner_world["client"].post(f"{BASE}/drafts/{c[0]['draft']['id']}/send")
    assert r.status_code == 409 and "today" in r.json()["detail"]
    assert len(relayed(outbox)) == PER_RECIPIENT_PER_DAY


def test_nothing_about_the_briefing_is_pushed_or_put_in_the_owners_chat(owner_world, ava, script, drafter, outbox):
    meera(ava, script, INJECTION)
    go_away()
    get(owner_world)
    assert [m for m in outbox["telegram"].sent if INJECTION[:20] in m.get("text", "")] == []
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Event).where(Event.content.contains("ignore previous"),
                                                                       Event.direction == "out")) == 0  # fmt: skip


# --- who can touch it ----------------------------------------------------------------------------------------------


def test_signed_out_and_unknown_drafts(owner_world):
    import uuid

    from fastapi.testclient import TestClient

    anon = TestClient(owner_world["client"].app, headers={"X-Requested-With": "superaide"})
    for method, path in (("get", BASE), ("post", f"{BASE}/seen"), ("post", f"{BASE}/drafts/{uuid.uuid4()}/send")):
        assert getattr(anon, method)(path).status_code == 401
    assert owner_world["client"].post(f"{BASE}/drafts/{uuid.uuid4()}/send").status_code == 404
    assert owner_world["client"].post(f"{BASE}/drafts/{uuid.uuid4()}/dismiss").status_code == 404
    assert owner_world["client"].post(f"{BASE}/drafts/not-an-id/send").status_code == 422


def test_a_known_person_is_not_drafted_for(owner_world, ava, script, drafter):
    """Someone Ava has since linked to a person is no longer a stranger's message to answer from here."""
    meera(ava, script)
    go_away()
    with session_scope() as s:
        from superaide.identity.models import ChannelIdentity, Person

        ident = s.scalar(select(ChannelIdentity).where(ChannelIdentity.stable_key == "U_MEERA"))
        p = Person(display_name="Meera Iyer", relation="external")
        s.add(p)
        s.flush()
        ident.person_id = p.id
    assert get(owner_world, force="true")["conversations"] == []
