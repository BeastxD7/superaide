"""Import lease (#133): third-party cards under a time-limited, erasable, owner-only lease."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from superaide.context.audience import Audience, visible_memories
from superaide.db.models import MemoryItem, Person, UserAccount
from superaide.db.session import session_scope
from superaide.imports import convert
from superaide.imports.models import ImportLease
from tests.api.conftest import OWNER

LEASES = "/app/import-leases"


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "import_lease_enabled", True)


def card(text: str, scope: str = "chat", **extra) -> dict:
    return {"scope": scope, "text": text, **extra}


def open_lease(owner, scopes=("chat",), days=7) -> str:
    r = owner.post(LEASES, json={"source_kind": "chat_archive", "scopes": list(scopes), "days": days, "label": "x"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def owner_person() -> uuid.UUID:
    with session_scope() as s:
        return s.scalar(select(UserAccount.person_id).where(UserAccount.email_normalized == OWNER["email"]))


def leased_rows(lease_id: str) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(MemoryItem).where(MemoryItem.lease_id == uuid.UUID(lease_id)))


def test_off_by_default_everything_is_404(owner):
    assert owner.get(LEASES).status_code == 404
    assert owner.post(LEASES, json={"source_kind": "notes", "scopes": ["notes"]}).status_code == 404


def test_signed_out_and_strangers_are_refused_even_when_on(on, browser, other):
    assert browser().get(LEASES).status_code == 401
    assert other.get(LEASES).status_code == 403


def test_cards_land_private_lowest_rank_and_tagged_with_the_lease(on, owner):
    lid = open_lease(owner)
    r = owner.post(f"{LEASES}/{lid}/cards", json={"cards": [card("Planning a trip to Lisbon in May", title="Trip")]})
    assert r.json() == {"added": 1}
    with session_scope() as s:
        row = s.scalar(select(MemoryItem).where(MemoryItem.lease_id == uuid.UUID(lid)))
        assert row.content == "Trip: Planning a trip to Lisbon in May"
        assert row.allowed_kind == "persons" and row.allowed_person_ids == [owner_person()]
        assert row.confidence == 0.4 and row.source == "imported"
        lease = s.get(ImportLease, uuid.UUID(lid))
        assert row.valid_to == lease.expires_at
    shown = owner.get(LEASES).json()[0]
    assert shown["cards"] == 1 and shown["cards_left"] == 1 and shown["status"] == "active"


def test_imported_cards_never_reach_anyone_but_the_owner(on, owner):
    lid = open_lease(owner)
    owner.post(f"{LEASES}/{lid}/cards", json={"cards": [card("Secret import card about Lisbon")]})
    with session_scope() as s:
        colleague = Person(display_name="Dev", relation="colleague")
        s.add(colleague)
        s.flush()
        others = visible_memories(s, Audience({colleague.id}, False, 2, True))
        group = visible_memories(s, Audience({owner_person()}, True, 0, False))  # a group chat with strangers in it
        mine = visible_memories(s, Audience({owner_person()}, False, 2, True))
        assert not [m for m in others + group if m.lease_id]
        assert [m for m in mine if m.lease_id]


def test_dates_and_order_numbers_are_not_phone_numbers(on, owner):
    lid = open_lease(owner)
    r = owner.post(
        f"{LEASES}/{lid}/cards",
        json={"cards": [card("Met the landlord", title="2026-03-02"), card("Order 20261023 arrived on 2026-10-23")]},
    )
    assert r.json() == {"added": 2}


def test_the_server_takes_only_the_card_schema(on, owner):
    lid = open_lease(owner)
    url = f"{LEASES}/{lid}/cards"
    assert owner.post(url, json={"cards": [card("ok", surprise="field")]}).status_code == 422  # unknown field
    assert owner.post(url, json={"cards": [card("x" * 601)]}).status_code == 422  # not a pasted document
    assert owner.post(url, json={"cards": []}).status_code == 422
    assert owner.post(url, json={"cards": [card("ok")] * 501}).status_code == 422
    assert owner.post(url, json={"raw": "BEGIN:VCALENDAR..."}).status_code == 422  # no raw files
    assert owner.post(url, json={"cards": [{"scope": "chat"}]}).status_code == 422
    assert owner.post(LEASES, json={"source_kind": "notes", "scopes": ["everything"]}).status_code == 422
    assert owner.post(LEASES, json={"source_kind": "notes", "scopes": ["notes"], "days": 400}).status_code == 422
    assert leased_rows(lid) == 0


def test_cards_outside_the_scopes_with_contact_details_or_known_already_are_left_out(on, owner):
    lid = open_lease(owner, scopes=("chat",))
    owner.post("/app/onboarding", json={"step": "hello", "answer": {"choice": "Nice to meet you"}})
    with session_scope() as s:
        from superaide.knowledge.store import remember_fact

        remember_fact(s, owner=s.get(Person, owner_person()), content="I live in Singapore", category="about")
    r = owner.post(
        f"{LEASES}/{lid}/cards",
        json={
            "cards": [
                card("Likes oat milk"),
                card("Heart rate was 61", scope="health"),
                card("Priya's email is priya@example.com"),
                card("Call +65 8123 4567 about the van"),
                card("I live in Singapore"),
                card("likes oat milk"),
            ]
        },
    )
    assert r.json() == {
        "added": 1,
        "skipped_outside_scope": 1,
        "skipped_contact_details": 2,
        "skipped_already_known": 2,
    }
    assert leased_rows(lid) == 1


def test_ending_the_lease_erases_every_card_and_nothing_else(on, owner):
    keep = open_lease(owner)
    lid = open_lease(owner)
    owner.post(f"{LEASES}/{keep}/cards", json={"cards": [card("Keep this one")]})
    owner.post(f"{LEASES}/{lid}/cards", json={"cards": [card("First card"), card("Second card")]})
    with session_scope() as s:
        from superaide.knowledge.store import remember_fact

        remember_fact(s, owner=s.get(Person, owner_person()), content="Told directly to Ava", category="about")
    r = owner.delete(f"{LEASES}/{lid}")
    assert r.json() == {"erased": 2}
    assert leased_rows(lid) == 0 and leased_rows(keep) == 1
    with session_scope() as s:
        assert s.scalar(select(MemoryItem).where(MemoryItem.content == "Told directly to Ava")) is not None
        assert s.get(ImportLease, uuid.UUID(lid)).status == "ended"
    assert owner.post(f"{LEASES}/{lid}/cards", json={"cards": [card("late")]}).status_code == 409


def test_an_expired_lease_is_swept_and_takes_its_cards_with_it(on, owner):
    lid = open_lease(owner)
    owner.post(f"{LEASES}/{lid}/cards", json={"cards": [card("Old card")]})
    with session_scope() as s:
        s.get(ImportLease, uuid.UUID(lid)).expires_at = datetime.now(UTC) - timedelta(minutes=1)
        s.execute(
            MemoryItem.__table__.update()
            .where(MemoryItem.lease_id == uuid.UUID(lid))
            .values(valid_to=datetime.now(UTC) - timedelta(minutes=1))
        )
    with session_scope() as s:  # even before the sweep it is not recalled
        assert not [m for m in visible_memories(s, Audience({owner_person()}, False, 2, True)) if m.lease_id]
    shown = owner.get(LEASES).json()[0]
    assert shown["status"] == "expired" and shown["cards_left"] == 0
    assert leased_rows(lid) == 0


def test_someone_elses_lease_is_404(on, owner, browser):
    lid = open_lease(owner)
    with session_scope() as s:
        stranger = Person(display_name="Other Owner", relation="manager")
        s.add(stranger)
        s.flush()
        from superaide.imports import lease as svc

        with pytest.raises(svc.LeaseError):
            svc.end_lease(s, stranger, uuid.UUID(lid))
    assert owner.delete(f"{LEASES}/{uuid.uuid4()}").status_code == 404
    assert owner.post(f"{LEASES}/{uuid.uuid4()}/cards", json={"cards": [card("x")]}).status_code == 404


# --- the local converters --------------------------------------------------------------


def test_chat_archive_converter_keeps_the_owners_first_message_only():
    archive = [
        {
            "title": "Trip planning",
            "create_time": 1_760_000_000,
            "mapping": {
                "a": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["Sure, tell me more."]}}},
                "b": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1,
                        "content": {"parts": ["I am flying to Lisbon in May"]},
                    }
                },
            },
        },
        {"title": "empty", "mapping": {}},
    ]
    out = convert.convert("chat-archive", json.dumps(archive))["cards"]
    assert out == [
        {
            "scope": "chat",
            "title": "Trip planning",
            "text": "I am flying to Lisbon in May",
            "category": "other",
            "when": "2025-10-09",
        }
    ]
    assert "Sure" not in json.dumps(out)
    with pytest.raises(ValueError):
        convert.convert("chat-archive", "not json")


def test_calendar_converter_drops_attendees_and_locations():
    ics = (
        "BEGIN:VEVENT\nSUMMARY:Dentist\nDTSTART;TZID=Asia/Singapore:20261105T100000\n"
        "ATTENDEE:mailto:a@b.com\nLOCATION:12 Home St\nEND:VEVENT"
    )
    out = convert.convert("calendar-ics", ics)["cards"]
    assert out == [
        {"scope": "calendar", "title": "", "text": "Calendar event: Dentist", "category": "other", "when": "2026-11-05"}
    ]


def test_converter_output_is_accepted_by_the_server(on, owner):
    lid = open_lease(owner, scopes=("notes",))
    batch = convert.convert("notes", "First paragraph about the garden.\n\n" + "long " * 400 + "\n\nThird one.")
    r = owner.post(f"{LEASES}/{lid}/cards", json=batch)
    assert r.status_code == 200 and r.json()["added"] == 3
