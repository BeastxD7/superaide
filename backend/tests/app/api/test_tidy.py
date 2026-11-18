"""/app/tidy (#415): the owner's change log of the nightly job and undo: theirs only, flag-gated, paged, reversible."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from superaide.identity.models import Person
from superaide.memory.consolidation import sleep
from superaide.memory.knowledge.store import remember_fact
from superaide.memory.models import ConsolidationChange, MemoryItem
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from tests.api_support import OTHER, OWNER, hire, signup
from tests.tenancy.api.test_many_owners import account

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture(autouse=True)
def flags_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    monkeypatch.setattr(get_settings(), "sleep_job_enabled", True)


def owners(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    return a, b


def tidy_night(email: str, texts: list[str]) -> list[str]:
    """Seed duplicate notes for this owner, run one night, and return the retired notes' ids."""
    owner_id = account(email).person_id
    with session_scope() as s:
        owner = s.get(Person, owner_id)
        for i, text in enumerate(texts):
            for variant in (text, text.upper()):
                m = remember_fact(s, owner=owner, content=variant, category="about", source="chat")
                if m is None:
                    m = MemoryItem(
                        content=variant, subject_person_ids=[owner_id], allowed_kind="persons",
                        allowed_person_ids=[owner_id], min_trust=2, source="document", category="about",
                    )  # fmt: skip
                    s.add(m)
                m.created_at = NOW - timedelta(days=10 - i)
        for i in range(8):
            remember_fact(
                s, owner=owner, content=f"Filler fact {i} about the weather.", category="about", source="chat"
            )
        s.flush()
        run = sleep.run_for_owner(s, owner, NOW, None, SimpleNamespace(model_triage="m"))
        return [str(i) for i in run.retired_ids]


def test_the_owner_sees_their_own_changes_with_the_note_text_and_why(browser):
    a, b = owners(browser)
    tidy_night(OWNER["email"], ["Priya Tan joined the Lisbon offsite."])
    tidy_night(OTHER["email"], ["SECRET-OF-THE-OTHER-OWNER"])
    r = a.get("/app/tidy")
    assert r.status_code == 200
    (item,) = r.json()["items"]
    assert item["kind"] == "duplicate" and item["can_undo"] is True and item["undone_at"] is None
    assert "lisbon" in item["retired"]["content"].lower() and item["kept"]["content"] and "capitals" in item["reason"]
    assert "SECRET-OF-THE-OTHER-OWNER" not in r.text
    assert "Lisbon" not in b.get("/app/tidy").text


def test_undo_restores_the_note_and_the_list_says_so_and_it_works_once(browser):
    a, _ = owners(browser)
    (retired,) = tidy_night(OWNER["email"], ["Rahul Verma owns the budget."])
    item = a.get("/app/tidy").json()["items"][0]
    r = a.post(f"/app/tidy/{item['id']}/undo")
    assert r.status_code == 200 and r.json()["status"] == "undone"
    got = a.get("/app/tidy").json()["items"][0]
    assert got["undone_at"] is not None and got["can_undo"] is False
    assert a.post(f"/app/tidy/{item['id']}/undo").status_code == 409
    with session_scope() as s:
        assert s.get(MemoryItem, uuid.UUID(retired)).valid_to is None
        assert (
            s.scalar(select(func.count()).select_from(ConsolidationChange).where(ConsolidationChange.kind == "undo"))
            == 1
        )


def test_another_owner_cannot_undo_and_an_unknown_id_is_404(browser):
    a, b = owners(browser)
    tidy_night(OWNER["email"], ["Meera Iyer is on leave."])
    item = a.get("/app/tidy").json()["items"][0]
    assert b.post(f"/app/tidy/{item['id']}/undo").status_code == 404
    assert a.post(f"/app/tidy/{uuid.uuid4()}/undo").status_code == 404
    with session_scope() as s:  # nothing happened to the note
        assert s.get(MemoryItem, uuid.UUID(item["retired"]["id"])).valid_to is not None


def test_a_note_the_owner_forgot_since_cannot_be_undone(browser):
    a, _ = owners(browser)
    tidy_night(OWNER["email"], ["Ananya Menon prefers mornings."])
    item = a.get("/app/tidy").json()["items"][0]
    with session_scope() as s:
        s.get(MemoryItem, uuid.UUID(item["retired"]["id"])).valid_to = NOW + timedelta(days=2)
    assert a.get("/app/tidy").json()["items"][0]["can_undo"] is False
    assert a.post(f"/app/tidy/{item['id']}/undo").status_code == 409


def test_the_list_is_paged_newest_first(browser):
    a, _ = owners(browser)
    tidy_night(OWNER["email"], [f"Distinct fact number {i} about Jordan Whitfield." for i in range(3)])
    first = a.get("/app/tidy?limit=1").json()
    assert len(first["items"]) == 1 and first["has_more"] is True and first["next_cursor"]
    second = a.get(f"/app/tidy?limit=2&cursor={first['next_cursor']}").json()
    assert len(second["items"]) == 2 and second["has_more"] is False
    ids = [first["items"][0]["id"], *(i["id"] for i in second["items"])]
    assert len(set(ids)) == 3
    assert a.get("/app/tidy?cursor=garbage").status_code == 422


def test_it_answers_404_while_the_sleep_job_is_off(browser, monkeypatch):
    a, _ = owners(browser)
    tidy_night(OWNER["email"], ["Priya Tan joined the Lisbon offsite."])
    item = a.get("/app/tidy").json()["items"][0]
    monkeypatch.setattr(get_settings(), "sleep_job_enabled", False)
    assert a.get("/app/tidy").status_code == 404
    assert a.post(f"/app/tidy/{item['id']}/undo").status_code == 404


def test_the_tidy_routes_need_the_owner(browser):
    anonymous, unhired = browser(), browser()
    signup(unhired, name="Mallory Tan", email="mallory@example.com")
    for method, path in (("GET", "/app/tidy"), ("POST", f"/app/tidy/{uuid.uuid4()}/undo")):
        assert anonymous.request(method, path).status_code == 401
        assert unhired.request(method, path).status_code == 403
