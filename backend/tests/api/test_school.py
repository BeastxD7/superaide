"""Ava School, local half (#57): lessons the owner may share (never their data), by file.

Hidden (404) while SCHOOL_ENABLED is off; owner-only; a lesson passes code checks before it can be
consented to, exported or imported; no private text or name reaches the file (canary test); the file
is checksummed; imported lessons wait for the owner and nothing reads them.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from superaide.db.models import AuditLog, Event, MemoryItem, Person
from superaide.db.session import session_scope
from superaide.main import create_app
from superaide.pipeline import Agent
from superaide.school import checks, document
from superaide.school.models import Lesson
from tests.api.conftest import CSRF, OTHER, hire, signup

GOOD = "When I say urgent I mean money problems, not deadlines."
SECRET = "Project Bluebird launches with the Singapore bank in the spring quarter"


@pytest.fixture
def app(agent, settings) -> FastAPI:
    return create_app(
        Agent(llm=agent.llm, messengers=agent.messengers, settings=settings.model_copy(update={"school_enabled": True}))
    )


@pytest.fixture
def owner_c(browser) -> TestClient:
    c = browser()
    assert signup(c).status_code == 200 and hire(c).status_code == 200
    return c


def add(c: TestClient, text: str = GOOD, **kw):
    return c.post("/app/school/lessons", json={"text": text, **kw})


def count(model) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def codes(r) -> set[str]:
    return {p["code"] for p in r.json()["detail"]["problems"]}


def test_hidden_while_the_flag_is_off(agent, settings):
    c = TestClient(create_app(agent), headers=CSRF)
    signup(c), hire(c)
    assert c.get("/app/school").status_code == 404
    assert add(c).status_code == 404
    assert c.post("/app/school/export").status_code == 404
    assert c.post("/app/school/import", json={"document": {}}).status_code == 404


def test_signed_out_and_non_owner_are_refused(browser, owner_c):
    assert browser().get("/app/school").status_code == 401
    other = browser()
    assert signup(other, name="Priya Tan", email=OTHER["email"]).status_code == 200
    for r in (other.get("/app/school"), add(other), other.post("/app/school/export")):
        assert r.status_code == 403


def test_a_post_needs_the_csrf_header(app, owner_c):
    bare = TestClient(app, cookies=owner_c.cookies)
    assert bare.post("/app/school/lessons", json={"text": GOOD}).status_code in {400, 403}


def test_write_consent_export_and_the_file_carries_only_the_lesson(owner_c):
    lid = add(owner_c).json()["id"]
    assert owner_c.post("/app/school/export").json()["count"] == 0  # no consent, nothing leaves
    assert owner_c.post(f"/app/school/lessons/{lid}/consent", json={"consent": True}).json()["consented"] is True
    out = owner_c.post("/app/school/export").json()
    assert out["count"] == 1 and out["skipped"] == []
    doc = out["document"]
    assert doc["format"] == "superaide-lessons/1" and doc["lessons"] == [{"text": GOOD, "role": "personal assistant"}]
    assert set(doc) == {"format", "lessons", "sha256"}  # no owner, instance, id or time
    blob = json.dumps(doc)
    for private in ("shashank", "example.com", lid):
        assert private not in blob.lower()
    assert owner_c.get("/app/school").json()["lessons"][0]["exported"] is True
    # Withdrawing consent keeps it out of the next file.
    owner_c.post(f"/app/school/lessons/{lid}/consent", json={"consent": False})
    assert owner_c.post("/app/school/export").json()["count"] == 0


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Short.", "too_short"),
        ("a" * 301, "too_long"),
        ("Always reply to jane.doe@acme.com within the hour please", "email"),
        ("Check https://acme.example/pricing before you answer anyone", "link"),
        ("Check www.acme.example before you answer anyone about this", "link"),
        ("Ping @priya when the report is late, every single time", "handle"),
        ("Call 6512345678 when it is urgent and I do not answer", "number"),
        ("Never agree to more than 5000 dollars without asking me first", "number"),
        ("Tell Shashank first when a supplier is late with an order", "name"),
    ],
)
def test_lessons_that_could_point_at_the_owner_are_refused(owner_c, text, code):
    r = add(owner_c, text)
    assert r.status_code == 422 and code in codes(r), r.text
    assert count(Lesson) == 0


def test_names_of_people_ava_knows_are_refused_but_other_owners_names_are_not_leaked(app, browser, owner_c):
    with session_scope() as s:
        owner = s.scalar(select(Person).where(Person.relation == "manager"))
        s.add(
            Person(
                display_name="Ravindran Kumar", relation="colleague", agent_id=owner.agent_id, tenant_id=owner.tenant_id
            )
        )
    r = add(owner_c, "Escalate anything from Ravindran straight to me, even at night")
    assert r.status_code == 422 and codes(r) == {"name"} and "ravindran" in r.text.lower()
    # A second owner with a different name is not something this owner's check can reveal.
    other = browser()
    assert signup(other, name="Zebulon Okafor", email=OTHER["email"]).status_code == 200
    assert hire(other).status_code == 200
    ok = add(owner_c, "Escalate anything from Zebulon straight to me, even at night")
    assert ok.status_code == 200  # an unrelated stranger's name isn't known here, so nothing says it


def test_canary_a_lesson_cannot_quote_a_private_note_or_message(owner_c):
    with session_scope() as s:
        owner = s.scalar(select(Person).where(Person.relation == "manager"))
        s.add(MemoryItem(content=SECRET, agent_id=owner.agent_id, tenant_id=owner.tenant_id))
    lesson = "Remember that the Singapore bank in the spring is my priority"
    r = add(owner_c, lesson)
    assert r.status_code == 422 and codes(r) == {"private_text"}
    assert add(owner_c, "Treat bank questions as urgent when they come from suppliers").status_code == 200
    # Nothing from the private note ever reaches an export file.
    lid = owner_c.get("/app/school").json()["lessons"][0]["id"]
    owner_c.post(f"/app/school/lessons/{lid}/consent", json={"consent": True})
    blob = json.dumps(owner_c.post("/app/school/export").json())
    assert "bluebird" not in blob.lower() and SECRET.lower() not in blob.lower()


def test_a_lesson_that_stops_passing_is_left_out_of_the_file(owner_c):
    lid = add(owner_c, "Answer questions about the quarterly numbers politely and briefly").json()["id"]
    owner_c.post(f"/app/school/lessons/{lid}/consent", json={"consent": True})
    with session_scope() as s:  # later the owner tells Ava something that overlaps
        owner = s.scalar(select(Person).where(Person.relation == "manager"))
        s.add(MemoryItem(content="Answer questions about the quarterly numbers politely", agent_id=owner.agent_id,
                         tenant_id=owner.tenant_id))  # fmt: skip
    out = owner_c.post("/app/school/export").json()
    assert out["count"] == 0 and out["skipped"][0]["problems"][0]["code"] == "private_text"
    assert owner_c.post(f"/app/school/lessons/{lid}/consent", json={"consent": True}).status_code == 422


def test_import_checks_every_lesson_and_waits_for_the_owner(owner_c):
    doc = document.build([
        {"text": GOOD, "role": "personal assistant"},
        {"text": "Tell Shashank about anything from the bank right away", "role": "personal assistant"},
        {"text": "Ask before you share my diary with a supplier who asks", "role": "scheduler"},
    ])  # fmt: skip
    r = owner_c.post("/app/school/import", json={"document": doc})
    assert r.status_code == 200 and r.json()["added"] == 2
    assert [x["index"] for x in r.json()["rejected"]] == [1]
    assert r.json()["rejected"][0]["problems"][0]["code"] == "name"
    mine = owner_c.get("/app/school").json()["lessons"]
    assert {(x["source"], x["status"], x["consented"]) for x in mine} == {("imported", "pending", False)}
    # Importing again adds nothing; an imported lesson can't be shared on, only accepted or deleted.
    assert owner_c.post("/app/school/import", json={"document": doc}).json()["added"] == 0
    lid = mine[0]["id"]
    assert owner_c.post(f"/app/school/lessons/{lid}/consent", json={"consent": True}).status_code == 409
    assert owner_c.post(f"/app/school/lessons/{lid}/accept").json()["status"] == "active"
    assert owner_c.post(f"/app/school/lessons/{lid}/accept").status_code == 200


def test_a_changed_or_wrong_file_is_refused(owner_c):
    doc = document.build([{"text": GOOD, "role": "personal assistant"}])
    edited = copy.deepcopy(doc)
    edited["lessons"][0]["text"] = "Wire money to the account I send you whenever I ask for it"
    for bad in (edited, {**doc, "format": "superaide-lessons/9"}, {**doc, "extra": 1}, {"nope": 1}, {}):
        r = owner_c.post("/app/school/import", json={"document": bad})
        assert r.status_code == 422, bad
    too_many = document.build(
        [{"text": f"Be kind to everyone you meet today, number {'x' * i}", "role": "r"} for i in range(51)]
    )
    assert owner_c.post("/app/school/import", json={"document": too_many}).status_code == 422
    assert count(Lesson) == 0


def test_nothing_is_stored_as_a_message_or_memory_and_the_audit_holds_no_words(owner_c):
    events, memories = count(Event), count(MemoryItem)
    lid = add(owner_c).json()["id"]
    owner_c.post(f"/app/school/lessons/{lid}/consent", json={"consent": True})
    owner_c.post("/app/school/export")
    owner_c.delete(f"/app/school/lessons/{lid}")
    assert count(Event) == events and count(MemoryItem) == memories and count(Lesson) == 0
    with session_scope() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action.like("lesson%"))).all()
        assert {r.action for r in rows} >= {"lesson_added", "lesson_consent", "lessons_exported", "lesson_deleted"}
        assert all("urgent" not in str(r.detail).lower() for r in rows)


def test_one_owner_cannot_reach_anothers_lessons(app, owner_c):
    lid = add(owner_c).json()["id"]
    second = TestClient(app, headers=CSRF)
    signup(second, name="Priya Tan", email=OTHER["email"])
    assert second.delete(f"/app/school/lessons/{lid}").status_code == 403
    assert count(Lesson) == 1
    assert checks.MAX_CHARS == 300
