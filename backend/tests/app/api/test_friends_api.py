"""The owner's side of friends (#479, #504): the identifier shown, approve and decline, listed accounts, invites."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.identity.models import ChannelIdentity, FriendCandidate
from superaide.memory.knowledge.store import add_contact
from superaide.memory.models import Contact
from superaide.platform.db.session import session_scope
from tests.api_support import OTHER, signup


def seed_pending() -> dict[str, str]:
    with session_scope() as s:
        me = hired_owner(s)
        contact = add_contact(s, owner=me, name="Meera Iyer", relationship="friend")
        ident = ChannelIdentity(
            provider="telegram", stable_key="4242", aux_handles={"username": "meera_i", "display_name": "Meera"}
        )
        s.add(ident)
        s.flush()
        s.add(FriendCandidate(identity_id=ident.id, contact_id=contact.id, via="handle"))
        s.flush()
        return {"contact": str(contact.id), "identity": str(ident.id), "person": str(contact.person_id)}


def test_pending_shows_the_immutable_identifier_and_approve_links(owner):
    ids = seed_pending()
    [row] = owner.get("/app/friends/pending").json()["pending"]
    assert row["platform_id"] == "4242" and row["provider"] == "telegram"
    assert row["username"] == "meera_i" and row["contact"]["name"] == "Meera Iyer"
    assert owner.get(f"/app/people/{ids['contact']}").json()["tier"] == "unverified_friend"
    assert owner.post(f"/app/friends/{ids['identity']}/approve").status_code == 200
    assert owner.get("/app/friends/pending").json()["pending"] == []
    assert owner.get(f"/app/people/{ids['contact']}").json()["tier"] == "verified_friend"
    with session_scope() as s:
        assert str(s.get(ChannelIdentity, uuid.UUID(ids["identity"])).person_id) == ids["person"]
    assert owner.post(f"/app/friends/{ids['identity']}/approve").status_code == 404  # nothing left to approve


def test_decline_keeps_a_stranger_and_cannot_then_be_approved(owner):
    ids = seed_pending()
    assert owner.post(f"/app/friends/{ids['identity']}/decline").status_code == 200
    assert owner.post(f"/app/friends/{ids['identity']}/approve").status_code == 404
    with session_scope() as s:
        assert s.get(ChannelIdentity, uuid.UUID(ids["identity"])).person_id is None


def test_unknown_removed_and_linked_accounts_cannot_be_approved(owner):
    ids = seed_pending()
    assert owner.post(f"/app/friends/{uuid.uuid4()}/approve").status_code == 404
    assert owner.post("/app/friends/not-a-uuid/approve").status_code == 422
    with session_scope() as s:
        s.get(ChannelIdentity, uuid.UUID(ids["identity"])).risk_flags = ["external"]
    assert owner.post(f"/app/friends/{ids['identity']}/approve").status_code == 409  # another company's account
    with session_scope() as s:
        s.get(ChannelIdentity, uuid.UUID(ids["identity"])).risk_flags = []
        s.get(Contact, uuid.UUID(ids["contact"])).removed_at = datetime.now(UTC)
    assert owner.post(f"/app/friends/{ids['identity']}/approve").status_code == 404


def test_handles_are_validated_listed_and_removed(owner):
    ids = seed_pending()
    url = f"/app/people/{ids['contact']}/handles"
    r = owner.post(url, json={"provider": "telegram", "handle": "@Meera_I"})
    assert r.status_code == 200 and r.json()["handles"][0]["value"] == "meera_i"
    assert owner.get(f"/app/people/{ids['contact']}").json()["handles"][0]["kind"] == "username"
    assert owner.post(url, json={"provider": "telegram", "handle": "no"}).status_code == 422
    assert owner.post(url, json={"provider": "slack", "handle": "Meera Iyer"}).status_code == 422
    assert owner.post(url, json={"provider": "telegram", "handle": "x", "authority": "manager"}).status_code == 422
    hid = r.json()["handles"][0]["id"]
    assert owner.post(f"{url}/{hid}/remove").json()["handles"] == []
    assert owner.post(f"{url}/{hid}/remove").status_code == 404


def test_the_invite_says_when_telegram_is_not_connected(owner):
    ids = seed_pending()
    r = owner.post(f"/app/people/{ids['contact']}/invite")
    assert r.status_code == 200 and r.json() == {"available": False}
    assert owner.post(f"/app/people/{uuid.uuid4()}/invite").status_code == 404


def test_another_account_cannot_see_or_decide_anything(owner, browser):
    ids = seed_pending()
    stranger = browser()
    assert signup(stranger, **OTHER).status_code == 200
    for r in (
        stranger.get("/app/friends/pending"),
        stranger.post(f"/app/friends/{ids['identity']}/approve"),
        stranger.post(f"/app/people/{ids['contact']}/handles", json={"provider": "telegram", "handle": "zed_nobody"}),
        stranger.post(f"/app/people/{ids['contact']}/invite"),
    ):
        assert r.status_code == 403 and "4242" not in r.text and "Meera" not in r.text
    with session_scope() as s:
        assert s.scalar(select(FriendCandidate.state)) == "pending"
