"""The memory passport (docs/memory-passport.md): export, validated import, and erasing an import."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import Contact, Document, MemoryImport, MemoryItem, Person
from superaide.db.session import session_scope
from superaide.knowledge.store import add_contact, remember_fact
from superaide.passport import schema
from superaide.tenancy.context import InstanceScope, use_instance
from tests.api_support import OTHER, OWNER, hire, signup
from tests.tenancy.api.test_many_owners import account, instance_of

SECRET_OF_ANOTHER = "Priya is planning a surprise party for Ravi"
LONG_AGO = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def many_owners_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)


def owners(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    return a, b


def scope_of(email: str) -> InstanceScope:
    inst = instance_of(email)
    return InstanceScope(tenant_id=inst.tenant_id, agent_id=inst.id)


def seed_a() -> dict[str, str]:
    """A's memory: facts (one dated long ago, one shareable), a file with a fact from it, a contact,
    something A forgot, and a fact that belongs to someone else."""
    owner_id = account(OWNER["email"]).person_id
    with session_scope() as s:
        owner = s.get(Person, owner_id)
        assert owner is not None
        old = remember_fact(s, owner=owner, content="Jordan founded Acme Labs in Singapore.", category="work")
        assert old is not None
        old.valid_from = LONG_AGO
        remember_fact(s, owner=owner, content="Jordan prefers calls before 11am.", category="preference")
        remember_fact(s, owner=owner, content="Jordan is free on Fridays.", category="availability", shareable=True)
        gone = remember_fact(s, owner=owner, content="Jordan's old flat code is 1234.", category="other")
        assert gone is not None
        gone.valid_to = datetime.now(UTC)  # forgotten: must never leave
        doc = Document(
            owner_person_id=owner_id,
            kind="text",
            title="bio.txt",
            source="bio.txt",
            sha256="a" * 64,
            text="Jordan runs Acme Labs.",
            status="read",
        )
        s.add(doc)
        s.flush()
        remember_fact(
            s, owner=owner, content="Acme Labs builds AI assistants.", category="work", source="document", document=doc
        )
        add_contact(s, owner=owner, name="Arjun Mehta", relationship="team", vip=True, notes="Runs the ops team")
        other = Person(display_name="Priya Tan", relation="contact")
        s.add(other)
        s.flush()
        remember_fact(s, owner=other, content=SECRET_OF_ANOTHER, category="about")
        # A fact about both the owner and someone else, learned in someone else's conversation.
        shared = MemoryItem(
            content="Jordan and Priya are co-hosting a private dinner.",
            subject_person_ids=[owner_id, other.id],
            allowed_kind="persons",
            allowed_person_ids=[owner_id, other.id],
        )
        s.add(shared)
    return {}


def export_zip(client, **params) -> zipfile.ZipFile:
    r = client.get("/app/passport/export", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip" and "attachment" in r.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(r.content))


def jsonl(zf: zipfile.ZipFile, name: str) -> list[dict]:
    return [json.loads(x) for x in zf.read(name).decode().splitlines() if x.strip()]


def make_zip(facts=(), sources=(), people=(), manifest=None, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(manifest or {"format": schema.FORMAT, "schema_version": 1, "assistant": "Other Assistant"}),
        )
        zf.writestr("facts.jsonl", "".join(json.dumps(f) + "\n" for f in facts))
        zf.writestr("sources.jsonl", "".join(json.dumps(f) + "\n" for f in sources))
        zf.writestr("people.jsonl", "".join(json.dumps(f) + "\n" for f in people))
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def upload(client, data: bytes, *, confirm: bool, name: str = "passport.zip"):
    return client.post(
        "/app/passport/import",
        files={"file": (name, data, "application/zip")},
        data={"confirm": "true" if confirm else "false"},
    )


def notebook(client) -> list[dict]:
    return client.get("/app/notebook").json()["memories"]


# --- export ----------------------------------------------------------------------------------


def test_export_holds_only_the_owners_own_live_data(browser):
    a, _ = owners(browser)
    seed_a()
    zf = export_zip(a)
    assert set(zf.namelist()) == {"manifest.json", "facts.jsonl", "sources.jsonl", "people.jsonl", "preferences.json"}
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == schema.FORMAT and manifest["schema_version"] == 1
    facts = jsonl(zf, "facts.jsonl")
    texts = {f["content"] for f in facts}
    assert texts == {
        "Jordan founded Acme Labs in Singapore.",
        "Jordan prefers calls before 11am.",
        "Jordan is free on Fridays.",
        "Acme Labs builds AI assistants.",
    }
    assert manifest["counts"] == {"facts": 4, "sources": 1, "people": 1}
    everything = b"".join(zf.read(n) for n in zf.namelist()).decode()
    for forbidden in (SECRET_OF_ANOTHER, "Priya", "1234", "co-hosting", "password", "token"):
        assert forbidden not in everything, forbidden
    founded = next(f for f in facts if "founded" in f["content"])
    assert founded["valid_from"].startswith("2024-03-01") and founded["valid_to"] is None
    from_doc = next(f for f in facts if "assistants" in f["content"])
    source = jsonl(zf, "sources.jsonl")[0]
    assert from_doc["source_id"] == source["id"] and source["title"] == "bio.txt"
    assert jsonl(zf, "people.jsonl") == [{"name": "Arjun Mehta", "relationship": "team", "notes": "Runs the ops team"}]


def test_export_without_sources_leaves_file_text_out(browser):
    a, _ = owners(browser)
    seed_a()
    zf = export_zip(a, sources="false")
    assert jsonl(zf, "sources.jsonl") == []
    assert "Jordan runs Acme Labs." not in zf.read("facts.jsonl").decode()


# --- import ----------------------------------------------------------------------------------


def test_round_trip_into_a_fresh_account_keeps_facts_sources_and_valid_time(browser):
    a, b = owners(browser)
    seed_a()
    data = a.get("/app/passport/export").content

    preview = upload(b, data, confirm=False).json()
    assert preview["imported"] is False and preview["facts"] == 4 and preview["new_facts"] == 4
    assert preview["sources"] == 1 and preview["new_people"] == 1 and preview["skipped_count"] == 0
    assert notebook(b) == []  # a preview writes nothing

    done = upload(b, data, confirm=True).json()
    assert done["imported"] is True and uuid.UUID(done["import_id"])
    mem = {m["text"]: m for m in notebook(b)}
    assert set(mem) == {
        "Jordan founded Acme Labs in Singapore.",
        "Jordan prefers calls before 11am.",
        "Jordan is free on Fridays.",
        "Acme Labs builds AI assistants.",
    }
    assert mem["Acme Labs builds AI assistants."]["document"] == "bio.txt"
    assert all(m["source"] == "imported" for m in mem.values())
    assert all(m["private"] is True and m["shareable"] is False for m in mem.values())  # even the "shareable" one

    b_person = account(OTHER["email"]).person_id
    with use_instance(scope_of(OTHER["email"])), session_scope() as s:
        founded = s.scalar(select(MemoryItem).where(MemoryItem.content.contains("founded")))
        assert founded is not None
        assert founded.valid_from.date().isoformat() == "2024-03-01"
        assert founded.confidence <= schema.IMPORTED_CONFIDENCE_CAP
        assert founded.subject_person_ids == [b_person] and founded.allowed_person_ids == [b_person]
        assert founded.allowed_kind == "persons" and founded.min_trust == 2
        doc = s.scalar(select(Document).where(Document.title == "bio.txt"))
        assert doc is not None and doc.owner_person_id == b_person and doc.status == "stored"
        contact = s.scalar(select(Contact).where(Contact.owner_person_id == b_person))
        assert contact is not None and contact.vip is False  # VIP changes when Ava interrupts: the owner's call

    # And the passport B can now export says the same thing.
    again = export_zip(b)
    assert {f["content"] for f in jsonl(again, "facts.jsonl")} == set(mem)


def test_importing_never_overrides_or_duplicates_what_ava_knows(browser):
    a, b = owners(browser)
    seed_a()
    b.post("/app/chat", json={"text": "Remember that Jordan prefers calls after 3pm"})
    b.post("/app/chat", json={"text": "Remember that Jordan is free on fridays."})
    data = a.get("/app/passport/export").content
    preview = upload(b, data, confirm=False).json()
    assert preview["already_known_facts"] == 1  # "free on Fridays", said again
    assert upload(b, data, confirm=True).json()["imported"] is True
    texts = [m["text"] for m in notebook(b)]
    assert "Jordan prefers calls after 3pm" in texts  # what B told Ava is still there, unchanged
    assert sum("free on fridays" in t.lower() for t in texts) == 1
    # Importing the same file again adds nothing.
    assert upload(b, data, confirm=True).status_code == 409


def test_erasing_an_import_removes_exactly_what_it_brought(browser):
    a, b = owners(browser)
    seed_a()
    b.post("/app/chat", json={"text": "Remember that my own note stays"})
    imported = upload(b, a.get("/app/passport/export").content, confirm=True).json()
    assert len(notebook(b)) == 5
    listed = b.get("/app/passport/imports").json()["imports"]
    assert listed[0]["id"] == imported["import_id"] and listed[0]["facts"] == 4 and listed[0]["facts_left"] == 4

    r = b.post(f"/app/passport/imports/{imported['import_id']}/erase")
    assert r.status_code == 200 and r.json()["erased"] == {"facts": 4, "sources": 1, "people": 1}
    assert [m["text"] for m in notebook(b)] == ["my own note stays"]
    with use_instance(scope_of(OTHER["email"])), session_scope() as s:
        assert s.scalars(select(Document)).all() == []
        assert s.scalar(select(Person).where(Person.display_name == "Arjun Mehta")) is None
        rec = s.get(MemoryImport, uuid.UUID(imported["import_id"]))
        assert rec is not None and rec.erased_at is not None
    assert b.post(f"/app/passport/imports/{imported['import_id']}/erase").status_code == 404
    assert b.get("/app/passport/imports").json()["imports"][0]["erased"] is True


def test_nobody_can_erase_or_see_someone_elses_import(browser):
    a, b = owners(browser)
    seed_a()
    mine = upload(b, a.get("/app/passport/export").content, confirm=True).json()["import_id"]
    assert a.post(f"/app/passport/imports/{mine}/erase").status_code == 404
    assert a.get("/app/passport/imports").json() == {"imports": []}
    assert len(notebook(b)) == 4


def test_a_file_cannot_name_the_audience_or_the_subject(browser):
    """Every field that would decide who sees a fact is ignored: the importing owner is always the subject
    and the only audience, so an import cannot plant a fact in someone else's (or the public's) view."""
    a, b = owners(browser)
    other = str(uuid.uuid4())
    forged = {
        "content": "Planted fact",
        "category": "about",
        "subject_person_ids": [other],
        "allowed_kind": "public",
        "allowed_person_ids": [other],
        "min_trust": 0,
        "agent_id": other,
        "tenant_id": other,
        "shareable": True,
        "confidence": 1,
    }
    assert upload(b, make_zip(facts=[forged]), confirm=True).status_code == 200
    me = account(OTHER["email"]).person_id
    with use_instance(scope_of(OTHER["email"])), session_scope() as s:
        item = s.scalar(select(MemoryItem).where(MemoryItem.content == "Planted fact"))
        assert item is not None
        assert item.subject_person_ids == [me] and item.allowed_person_ids == [me]
        assert item.allowed_kind == "persons" and item.min_trust == 2 and item.confidence <= 0.6
        assert item.agent_id == scope_of(OTHER["email"]).agent_id
    assert not any("Planted" in m["text"] for m in notebook(a))


def test_imported_facts_never_reach_a_stranger_prompt(browser):
    """The audience gate sees an import as private to the owner: a public-audience query finds none of it."""
    from superaide.context.audience import Audience, visible_memories

    a, b = owners(browser)
    forged = {"content": "Imported private detail", "allowed_kind": "public", "shareable": True}
    upload(b, make_zip(facts=[forged]), confirm=True)
    with use_instance(scope_of(OTHER["email"])), session_scope() as s:
        stranger = Audience(person_ids=set(), has_unknown=True, min_trust=0, all_internal=False)
        assert visible_memories(s, stranger, limit=50) == []
        owner = Audience(person_ids={account(OTHER["email"]).person_id}, min_trust=2)
        assert [m.content for m in visible_memories(s, owner)] == ["Imported private detail"]


# --- validation ----------------------------------------------------------------------------------


def test_bad_files_are_refused_with_a_reason_and_write_nothing(browser):
    _, b = owners(browser)
    cases = {
        "not a zip": (b"hello", "I found no facts"),
        "no manifest": (make_zip(extra={}).replace(b"manifest.json", b"manifesx.json"), "no manifest.json"),
        "wrong format": (make_zip(manifest={"format": "other", "schema_version": 1}), "does not say"),
        "future version": (
            make_zip(manifest={"format": schema.FORMAT, "schema_version": 99}),
            "schema version 99",
        ),
        "no version": (make_zip(manifest={"format": schema.FORMAT}), "schema_version"),
        "corrupt zip": (b"PK\x03\x04garbage", "readable zip"),
    }
    for name, (data, reason) in cases.items():
        r = upload(b, data, confirm=True)
        assert r.status_code == 422, (name, r.text)
        assert reason in r.json()["detail"], (name, r.json())
    assert notebook(b) == []


def test_a_zip_bomb_and_oversize_uploads_are_stopped(browser, monkeypatch):
    _, b = owners(browser)
    monkeypatch.setattr(schema, "MAX_MEMBER_BYTES", 10_000)
    bomb = make_zip(facts=[{"content": "x" * 1500}] * 20)
    assert len(bomb) < 2000  # tiny on the wire, big once unpacked
    r = upload(b, bomb, confirm=True)
    assert r.status_code == 422 and "too large" in r.json()["detail"]
    monkeypatch.setattr(schema, "MAX_UPLOAD_BYTES", 100)
    assert upload(b, make_zip(facts=[{"content": "a fact"}]), confirm=True).status_code == 422


def test_bad_records_are_skipped_and_reported_not_imported(browser):
    _, b = owners(browser)
    facts = [
        {"content": "A good fact"},
        {"content": ""},
        {"nothing": "here"},
        {"content": "Already untrue", "valid_to": "2024-01-01T00:00:00Z"},
        {"content": "y" * 2001},
        {"content": "Odd\x00 chars\x07 are\ncleaned", "category": "not-a-category", "confidence": "high"},
    ]
    raw = make_zip(facts=facts, extra={"../../etc/passwd": b"x", "facts2.jsonl": b"{}"})
    preview = upload(b, raw, confirm=False).json()
    assert preview["facts"] == 2 and preview["skipped_count"] == 4
    assert len(preview["skipped"]) == 4
    upload(b, raw, confirm=True)
    texts = sorted(m["text"] for m in notebook(b))
    assert texts == ["A good fact", "Odd chars are cleaned"]
    assert {m["category"] for m in notebook(b)} == {"other"}


def test_a_bare_jsonl_from_another_assistant_is_accepted(browser):
    _, b = owners(browser)
    data = b'{"content": "Likes oat milk", "category": "preference"}\n{"content": "Lives in Pune"}\n'
    r = upload(b, data, confirm=True, name="facts.jsonl")
    assert r.status_code == 200 and r.json()["new_facts"] == 2
    assert {m["text"] for m in notebook(b)} == {"Likes oat milk", "Lives in Pune"}


def test_future_dates_cannot_be_planted(browser):
    _, b = owners(browser)
    upload(b, make_zip(facts=[{"content": "From the future", "valid_from": "2999-01-01T00:00:00Z"}]), confirm=True)
    with use_instance(scope_of(OTHER["email"])), session_scope() as s:
        item = s.scalar(select(MemoryItem).where(MemoryItem.content == "From the future"))
        assert item is not None and item.valid_from <= datetime.now(UTC)


def test_the_export_and_import_routes_need_the_owner(browser):
    a, _ = owners(browser)
    anonymous = browser()
    assert anonymous.get("/app/passport/export").status_code == 401
    assert upload(anonymous, b"x", confirm=True).status_code == 401
    assert anonymous.get("/app/passport/imports").status_code == 401
    unhired = browser()
    signup(unhired, name="Mallory Tan", email="mallory@example.com")
    assert unhired.get("/app/passport/export").status_code == 403
    assert upload(unhired, b"x", confirm=True).status_code == 403
    no_header = browser(csrf=False)
    no_header.cookies.update(a.cookies)
    assert upload(no_header, b"x", confirm=True).status_code in (400, 403)
