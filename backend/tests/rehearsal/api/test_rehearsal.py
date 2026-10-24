"""The rehearsal room (#58): practise a hard conversation; nothing leaves the owner.

Hidden (404) while REHEARSAL_ENABLED is off; owner-only; the model runs inside BudgetGuard; no
`event` or `memory_item` rows are written; the debrief cites only the owner's own words; an
unkept session is erased after a day and any session can be erased at once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from superaide.brain.budget import BudgetExceeded
from superaide.brain.llm import FakeLLM
from superaide.db.models import AuditLog, Event, MemoryItem
from superaide.db.session import session_scope
from superaide.features.rehearsal.models import RehearsalSession
from superaide.features.rehearsal.service import DebriefOutput, DebriefPoint
from superaide.main import create_app
from superaide.pipeline import Agent
from tests.api_support import CSRF, OTHER, hire, signup

SAID = "I need a 10 percent raise before the end of the year"
NOTES = "Goal: keep the team together. I will not accept less than 8 percent."


class Script:
    """Scripted model: the other side's line, and a debrief whose quotes the test chooses."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.quotes = [SAID, "keep the team together", "I never said this"]
        self.refuse = False

    def __call__(self, purpose, system, messages, output_model):
        self.calls.append({"purpose": purpose, "system": "\n".join(b.text for b in system), "messages": messages})
        if self.refuse:
            raise BudgetExceeded("today's chat limit of US$1 would be passed", "day")
        if output_model is DebriefOutput:
            return DebriefOutput(
                points=[
                    DebriefPoint(heading=f"Point {i}", advice="Be concrete.", quote=q)
                    for i, q in enumerate(self.quotes)
                ]
            )
        return "That is a big ask. What has changed since your last review?"


@pytest.fixture
def script() -> Script:
    return Script()


def app_with(agent, settings, script: Script, *, on: bool) -> FastAPI:
    a = Agent(
        llm=FakeLLM(script), messengers=agent.messengers, settings=settings.model_copy(update={"rehearsal_enabled": on})
    )
    return create_app(a)


@pytest.fixture
def app(agent, settings, script) -> FastAPI:
    return app_with(agent, settings, script, on=True)


@pytest.fixture
def owner_c(browser) -> TestClient:
    c = browser()
    assert signup(c).status_code == 200 and hire(c).status_code == 200
    return c


def start(c: TestClient, **kw) -> dict:
    r = c.post("/app/rehearsal", json={"role": "a sceptical manager", "notes": NOTES, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def count(model) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def test_hidden_while_the_flag_is_off(agent, settings, script):
    off = app_with(agent, settings, script, on=False)
    c = TestClient(off, headers=CSRF)
    signup(c), hire(c)
    assert c.get("/app/rehearsal").status_code == 404
    assert c.post("/app/rehearsal", json={"role": "an investor"}).status_code == 404
    assert c.delete("/app/rehearsal/6f1f5b8e-0000-4000-8000-000000000006").status_code == 404


def test_signed_out_and_non_owner_are_refused(browser, owner_c):
    assert browser().get("/app/rehearsal").status_code == 401
    other = browser()
    assert signup(other, name="Priya Tan", email=OTHER["email"]).status_code == 200
    assert other.get("/app/rehearsal").status_code == 403
    assert other.post("/app/rehearsal", json={"role": "an investor"}).status_code == 403


def test_a_post_needs_the_csrf_header(app, owner_c):
    bare = TestClient(app, cookies=owner_c.cookies)
    assert bare.post("/app/rehearsal", json={"role": "an investor"}).status_code in {400, 403}


def test_a_rehearsal_says_it_is_one_and_the_model_gets_only_the_session(owner_c, script):
    sid = start(owner_c)["id"]
    r = owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID})
    body = r.json()
    assert r.status_code == 200 and body["paused"] is False
    assert body["reply"].startswith("That is a big ask")
    assert "rehearsal" in body["session"]["notice"].lower() and "nothing is sent" in body["session"]["notice"].lower()
    call = script.calls[-1]
    assert call["purpose"] == "rehearsal" and "This is a rehearsal" in call["system"]
    assert NOTES in call["system"] and "a sceptical manager" in call["system"]
    assert call["messages"] == [{"role": "user", "content": SAID}]


def test_nothing_is_sent_stored_as_a_message_or_remembered(owner_c, outbox):
    events, memories = count(Event), count(MemoryItem)
    sid = start(owner_c)["id"]
    owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID})
    owner_c.post(f"/app/rehearsal/{sid}/end")
    owner_c.post(f"/app/rehearsal/{sid}/keep")
    assert count(Event) == events and count(MemoryItem) == memories
    assert all(not m.sent for m in outbox.values())


def test_the_debrief_keeps_only_points_quoting_the_owner(owner_c):
    sid = start(owner_c)["id"]
    owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID})
    r = owner_c.post(f"/app/rehearsal/{sid}/end")
    s = r.json()["session"]
    assert r.status_code == 200 and s["ended"] is True
    quotes = [p["quote"] for p in s["debrief"]["points"]]
    assert quotes == [SAID, "keep the team together"]  # the made-up quote is dropped
    assert s["debrief"]["dropped"] == 1
    # The role-player's own line is not the owner's wording either.
    assert all("big ask" not in q for q in quotes)


def test_a_quote_from_the_role_player_does_not_count(owner_c, script):
    script.quotes = ["That is a big ask", "What has changed since your last review"]
    sid = start(owner_c)["id"]
    owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID})
    s = owner_c.post(f"/app/rehearsal/{sid}/end").json()["session"]
    assert s["debrief"]["points"] == []


def test_a_refused_model_call_pauses_cleanly_and_saves_nothing(owner_c, script):
    sid = start(owner_c)["id"]
    script.refuse = True
    r = owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID})
    assert r.status_code == 200 and r.json()["paused"] is True and "paused" in r.json()["message"]
    assert owner_c.get(f"/app/rehearsal/{sid}").json()["turns"] == []
    assert owner_c.post(f"/app/rehearsal/{sid}/end").status_code == 409  # nothing said yet
    owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID})  # still refused, still 200
    assert owner_c.post(f"/app/rehearsal/{sid}/end").status_code == 409


def test_the_offline_echo_cannot_play_a_role(agent, settings):
    from superaide.brain.wiring import demo_responder

    a = Agent(llm=FakeLLM(demo_responder), messengers=agent.messengers,
              settings=settings.model_copy(update={"rehearsal_enabled": True}))  # fmt: skip
    c = TestClient(create_app(a), headers=CSRF)
    signup(c), hire(c)
    sid = start(c)["id"]
    r = c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID})
    assert r.json()["paused"] is True and "No AI model" in r.json()["message"]


def test_keep_and_erase(owner_c):
    sid = start(owner_c)["id"]
    owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID})
    assert owner_c.post(f"/app/rehearsal/{sid}/keep").json()["kept"] is True
    assert owner_c.delete(f"/app/rehearsal/{sid}").json() == {"ok": True}
    assert owner_c.get(f"/app/rehearsal/{sid}").status_code == 404
    assert count(RehearsalSession) == 0
    with session_scope() as s:  # the audit trail holds an id and an action, never the words
        rows = s.scalars(select(AuditLog).where(AuditLog.action.like("rehearsal_%"))).all()
        assert {r.action for r in rows} == {"rehearsal_started", "rehearsal_erased"}
        assert all(SAID not in str(r.detail) and NOTES not in str(r.detail) for r in rows)


def test_an_unkept_session_is_erased_after_a_day_but_a_kept_one_stays(owner_c):
    a, b = start(owner_c)["id"], start(owner_c)["id"]
    owner_c.post(f"/app/rehearsal/{b}/keep")
    old = datetime.now(UTC) - timedelta(days=2)
    with session_scope() as s:
        s.execute(update(RehearsalSession).values(created_at=old))
    ids = [x["id"] for x in owner_c.get("/app/rehearsal").json()["sessions"]]
    assert ids == [b] and a not in ids
    assert owner_c.get(f"/app/rehearsal/{a}").status_code == 404


def test_another_owners_session_is_not_reachable(browser, agent, settings, script):
    app = app_with(agent, settings, script, on=True)
    first = TestClient(app, headers=CSRF)
    signup(first), hire(first)
    sid = start(first)["id"]
    second = TestClient(app, headers=CSRF)
    signup(second, name="Priya Tan", email=OTHER["email"])
    assert second.get(f"/app/rehearsal/{sid}").status_code == 403  # not an owner of anything
    assert second.delete(f"/app/rehearsal/{sid}").status_code == 403
    assert count(RehearsalSession) == 1


def test_a_long_rehearsal_is_capped(owner_c, monkeypatch):
    from superaide.features.rehearsal import service

    monkeypatch.setattr(service, "MAX_TURNS", 2)
    sid = start(owner_c)["id"]
    for _ in range(2):
        assert owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID}).status_code == 200
    assert owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": SAID}).status_code == 409


def test_input_limits_and_nul_bytes(owner_c):
    assert owner_c.post("/app/rehearsal", json={"role": "x"}).status_code == 422
    assert owner_c.post("/app/rehearsal", json={"role": "an investor", "notes": "a" * 4001}).status_code == 422
    assert owner_c.post("/app/rehearsal", json={"role": "an inv\u0000estor"}).status_code in {400, 422}
    sid = start(owner_c)["id"]
    assert owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": ""}).status_code == 422
    assert owner_c.post(f"/app/rehearsal/{sid}/turn", json={"text": "a" * 2001}).status_code == 422
