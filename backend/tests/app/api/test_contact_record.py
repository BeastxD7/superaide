"""The owner's record of one person (#500): what Ava holds, where it came from, who may be told it; edit; forget."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.identity.models import ChannelIdentity, Person
from superaide.memory.knowledge.store import add_contact
from superaide.memory.models import Contact, MemoryItem
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from tests.api_support import OTHER, signup  # noqa: F401


def seed(**fact_fields) -> dict[str, str]:
    """Meera Iyer on the owner's list, one Telegram account, and one fact about her."""
    with session_scope() as s:
        me = hired_owner(s)
        contact = add_contact(s, owner=me, name="Meera Iyer", relationship="friend")
        s.add(
            ChannelIdentity(
                person_id=contact.person_id, provider="telegram", stable_key="4242", aux_handles={"username": "meera_i"}
            )
        )
        fact = MemoryItem(
            content="Meera prefers calls after 6pm.",
            subject_person_ids=[contact.person_id],
            allowed_kind="persons",
            allowed_person_ids=[me.id],
            category="person",
            source="telegram",
            **fact_fields,
        )
        s.add(fact)
        s.flush()
        return {"contact": str(contact.id), "person": str(contact.person_id), "fact": str(fact.id), "owner": str(me.id)}


def test_the_record_shows_facts_with_source_and_audience(owner):
    ids = seed()
    r = owner.get(f"/app/people/{ids['contact']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Meera Iyer" and body["relationship"] == "friend"
    assert body["channels"][0]["provider"] == "telegram" and body["channels"][0]["handle"] == "meera_i"
    [fact] = body["facts"]
    assert fact["content"] == "Meera prefers calls after 6pm."
    assert fact["source_label"] == "Told to Ava on Telegram"
    assert fact["audience"] == {"kind": "owner", "label": "Only you"}
    assert fact["about_them"] is True and fact["learned_at"]


def test_audience_labels_follow_the_row(owner):
    ids = seed(min_trust=0)
    with session_scope() as s:
        s.add(MemoryItem(content="Public bit", subject_person_ids=[uuid.UUID(ids["person"])], allowed_kind="public"))
        s.add(MemoryItem(content="Team bit", subject_person_ids=[uuid.UUID(ids["person"])], allowed_kind="org"))
    kinds = {f["content"]: f["audience"]["kind"] for f in owner.get(f"/app/people/{ids['contact']}").json()["facts"]}
    assert kinds["Public bit"] == "anyone" and kinds["Team bit"] == "team"


def test_edit_changes_only_the_fields_sent_and_is_audited(owner):
    ids = seed()
    r = owner.patch(f"/app/people/{ids['contact']}", json={"vip": True, "notes": "  Met at the offsite  "})
    assert r.status_code == 200, r.text
    assert (
        r.json()["vip"] is True and r.json()["notes"] == "Met at the offsite" and r.json()["relationship"] == "friend"
    )
    with session_scope() as s:
        row = s.get(Contact, uuid.UUID(ids["contact"]))
        assert row.vip and row.notes == "Met at the offsite"
        assert s.scalar(select(AuditLog.id).where(AuditLog.action == "contact_edited")) is not None


def test_edit_refuses_unknown_fields_and_bad_values(owner):
    ids = seed()
    url = f"/app/people/{ids['contact']}"
    assert owner.patch(url, json={"relation": "manager"}).status_code == 422  # authority is not editable here
    assert owner.patch(url, json={"relationship": "   "}).status_code == 422
    assert owner.patch(url, json={"relationship": "x" * 31}).status_code == 422
    assert owner.patch(url, json={"notes": "x" * 2001}).status_code == 422
    with session_scope() as s:
        assert s.get(Person, uuid.UUID(ids["person"])).relation != "manager"


def test_forget_is_a_soft_delete_and_leaves_the_row(owner):
    ids = seed()
    assert owner.post(f"/app/people/{ids['contact']}/facts/{ids['fact']}/forget").status_code == 200
    assert owner.get(f"/app/people/{ids['contact']}").json()["facts"] == []
    with session_scope() as s:
        row = s.get(MemoryItem, uuid.UUID(ids["fact"]))
        assert row is not None and row.valid_to is not None  # soft delete: kept, retired
    assert owner.post(f"/app/people/{ids['contact']}/facts/{ids['fact']}/forget").status_code == 404


def test_cannot_forget_a_fact_about_someone_else_through_this_contact(owner):
    ids = seed()
    with session_scope() as s:
        me = hired_owner(s)
        other_fact = MemoryItem(content="Owner secret", subject_person_ids=[me.id], allowed_person_ids=[me.id])
        s.add(other_fact)
        s.flush()
        secret = str(other_fact.id)
    assert owner.post(f"/app/people/{ids['contact']}/facts/{secret}/forget").status_code == 404
    with session_scope() as s:
        assert s.get(MemoryItem, uuid.UUID(secret)).valid_to is None


def test_unknown_removed_and_garbage_ids_are_404_or_422(owner):
    ids = seed()
    assert owner.get(f"/app/people/{uuid.uuid4()}").status_code == 404
    assert owner.get("/app/people/not-a-uuid").status_code == 422
    with session_scope() as s:
        s.get(Contact, uuid.UUID(ids["contact"])).removed_at = __import__("datetime").datetime.now(
            __import__("datetime").UTC
        )
    assert owner.get(f"/app/people/{ids['contact']}").status_code == 404


def test_another_account_never_sees_the_record(owner, browser):
    ids = seed()
    stranger = browser()
    assert signup(stranger, **OTHER).status_code == 200
    for r in (
        stranger.get(f"/app/people/{ids['contact']}"),
        stranger.patch(f"/app/people/{ids['contact']}", json={"vip": True}),
        stranger.post(f"/app/people/{ids['contact']}/facts/{ids['fact']}/forget"),
    ):
        assert r.status_code == 403
        assert "Meera" not in r.text and "6pm" not in r.text
    signed_out = browser()
    assert signed_out.get(f"/app/people/{ids['contact']}").status_code == 401
    assert signed_out.post(f"/app/people/{ids['contact']}/facts/{ids['fact']}/forget").status_code == 401
    assert browser(csrf=False).patch(f"/app/people/{ids['contact']}", json={"vip": True}).status_code in (401, 403)
