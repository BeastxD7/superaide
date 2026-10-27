"""The Glass brain share link (/app/brain/share-links, /p/brain/{token}, #55): unlisted, counts only, hashed at
rest, at most 7 days, revocable, and the same 404 for everything wrong."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from superaide.features.brainshare import service
from superaide.features.brainshare.models import BrainShare
from superaide.memory.knowledge.store import remember_fact
from superaide.platform.config import get_settings
from superaide.platform.db.models import Person
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, all_instances, use_instance
from superaide.platform.tenancy.models import AgentInstance, InstanceMember
from tests.api_support import OTHER, OWNER, hire, signup
from tests.tenancy.api.test_many_owners import account


@pytest.fixture(autouse=True)
def on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)
    monkeypatch.setattr(get_settings(), "brain_share_enabled", True)


def owners(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    return a, b


def seed(email: str, *texts: str) -> None:
    """Notes for that account's owner, written inside that account's own instance."""
    user = account(email)
    with all_instances(), session_scope() as s:
        member = s.scalar(select(InstanceMember).where(InstanceMember.user_id == user.id))
        assert member is not None
        instance = s.get(AgentInstance, member.agent_id)
        assert instance is not None
        scope = InstanceScope(tenant_id=instance.tenant_id, agent_id=instance.id)
    with use_instance(scope), session_scope() as s:
        owner = s.get(Person, user.person_id)
        assert owner is not None
        for t in texts:
            remember_fact(s, owner=owner, content=t, category="work")


def new_link(client, days: int | None = None) -> dict:
    r = client.post("/app/brain/share-links", json={} if days is None else {"days": days})
    assert r.status_code == 200, r.text
    return r.json()


def token_of(link: dict) -> str:
    return link["token"]


def test_a_link_shows_counts_only_never_names_or_notes(browser):
    a, _ = owners(browser)
    seed(OWNER["email"], "Jordan is leading Project Bluebird with Arjun.", "Jordan prefers calls before 11am.")
    link = new_link(a)
    anonymous = browser()
    meta = anonymous.get(f"/p/brain/{token_of(link)}")
    assert meta.status_code == 200
    assert meta.json()["things"] == 2 and meta.json()["topics"] == [{"topic": "work", "count": 2}]
    assert meta.json()["origins"]["you"] == 2
    svg = anonymous.get(f"/p/brain/{token_of(link)}/picture.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    for private in ("Jordan", "Bluebird", "Arjun", "11am", "Whitfield"):
        assert private not in meta.text and private not in svg.text, private
    assert "things Ava knows" in svg.text
    for r in (meta, svg):  # never cached, never indexed, no referrer
        assert r.headers["cache-control"] == "no-store" and "noindex" in r.headers["x-robots-tag"]


def test_the_token_is_stored_hashed_and_shown_once(browser):
    a, _ = owners(browser)
    link = new_link(a)
    token = token_of(link)
    assert token.startswith("sa_brain_") and len(token) > 40
    with all_instances(), session_scope() as s:
        row = s.scalar(select(BrainShare))
        assert row is not None and row.token_hash == service.hash_token(token) and token not in row.token_hash
        dump = s.execute(text("SELECT row_to_json(b)::text FROM brain_share b")).scalar() or ""
        assert token not in dump
    listed = a.get("/app/brain/share-links").json()
    assert token not in str(listed) and listed["links"][0]["status"] == "active"  # the list never shows it again


def test_a_link_lasts_one_to_seven_days_and_the_database_enforces_it(browser):
    a, _ = owners(browser)
    assert a.post("/app/brain/share-links", json={"days": 0}).status_code == 422
    assert a.post("/app/brain/share-links", json={"days": 8}).status_code == 422
    seven = new_link(a, 7)
    delta = datetime.fromisoformat(seven["expires_at"]) - datetime.fromisoformat(seven["created_at"])
    assert delta == timedelta(days=7)
    assert datetime.fromisoformat(new_link(a)["expires_at"]) - datetime.now(UTC) < timedelta(days=1, minutes=1)
    with all_instances(), session_scope() as s:
        row = s.scalar(select(BrainShare))
        assert row is not None
        ids = (row.tenant_id, row.agent_id, row.owner_person_id)
    now = datetime.now(UTC)
    with pytest.raises(Exception, match="ck_brain_share_max_7_days"), all_instances(), session_scope() as s:
        s.add(  # code that bypasses the route cannot write an 8 day link either
            BrainShare(
                tenant_id=ids[0],
                agent_id=ids[1],
                owner_person_id=ids[2],
                token_hash="f" * 64,
                created_at=now,
                expires_at=now + timedelta(days=8),
            )  # fmt: skip
        )


def test_revoking_stops_the_link_at_once_and_only_its_owner_can_revoke(browser):
    a, b = owners(browser)
    link = new_link(a)
    anonymous = browser()
    assert anonymous.get(f"/p/brain/{token_of(link)}").status_code == 200
    assert b.post(f"/app/brain/share-links/{link['id']}/revoke").status_code == 404  # not their link
    assert anonymous.get(f"/p/brain/{token_of(link)}").status_code == 200
    assert a.post(f"/app/brain/share-links/{link['id']}/revoke").json() == {"revoked": True}
    assert anonymous.get(f"/p/brain/{token_of(link)}").status_code == 404
    assert anonymous.get(f"/p/brain/{token_of(link)}/picture.svg").status_code == 404
    assert a.get("/app/brain/share-links").json()["links"][0]["status"] == "revoked"
    assert b.get("/app/brain/share-links").json()["links"] == []  # and the other owner never sees it listed


def test_every_wrong_token_gets_the_same_404(browser):
    a, _ = owners(browser)
    live = new_link(a)
    gone = new_link(a)
    a.post(f"/app/brain/share-links/{gone['id']}/revoke")
    old = new_link(a)
    with all_instances(), session_scope() as s:
        row = s.get(BrainShare, uuid.UUID(old["id"]))
        assert row is not None
        row.created_at, row.expires_at = datetime.now(UTC) - timedelta(days=3), datetime.now(UTC) - timedelta(days=2)
    anonymous = browser()
    wrong = (token_of(gone), token_of(old), "sa_brain_" + "x" * 43, "nonsense", "sa_brain_" + "a" * 500)
    answers = {(r.status_code, r.json()["detail"]) for r in (anonymous.get(f"/p/brain/{t}") for t in wrong)}
    assert answers == {(404, "Not Found")}  # revoked, expired, forged, malformed: indistinguishable
    assert anonymous.get(f"/p/brain/{token_of(live)}").status_code == 200


def test_a_link_shows_only_its_own_owners_counts(browser):
    a, b = owners(browser)
    seed(OWNER["email"], "Jordan runs a design studio.")
    seed(OTHER["email"], "Someone else tracks a secret project.", "And a second thing.", "And a third.")
    anonymous = browser()
    assert anonymous.get(f"/p/brain/{token_of(new_link(a))}").json()["things"] == 1
    assert anonymous.get(f"/p/brain/{token_of(new_link(b))}").json()["things"] == 3


def test_nothing_answers_while_the_flag_is_off(browser, monkeypatch):
    a, _ = owners(browser)
    link = new_link(a)
    monkeypatch.setattr(get_settings(), "brain_share_enabled", False)
    anonymous = browser()
    assert anonymous.get(f"/p/brain/{token_of(link)}").status_code == 404
    assert anonymous.get(f"/p/brain/{token_of(link)}/picture.svg").status_code == 404
    assert a.get("/app/brain/share-links").status_code == 404
    assert a.post("/app/brain/share-links", json={}).status_code == 404


def test_views_are_counted_and_a_link_is_rate_limited(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "brain_share_per_minute", 3)
    a, _ = owners(browser)
    link = new_link(a)
    anonymous = browser()
    codes = [anonymous.get(f"/p/brain/{token_of(link)}").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert a.get("/app/brain/share-links").json()["links"][0]["views"] == 3


def test_at_most_five_live_links_and_a_revoked_one_frees_a_place(browser):
    a, _ = owners(browser)
    made = [new_link(a) for _ in range(service.MAX_ACTIVE)]
    assert a.post("/app/brain/share-links", json={}).status_code == 409
    a.post(f"/app/brain/share-links/{made[0]['id']}/revoke")
    assert a.post("/app/brain/share-links", json={}).status_code == 200


def test_the_owner_routes_need_the_owner(browser):
    anonymous, unhired = browser(), browser()
    signup(unhired, name="Mallory Tan", email="mallory@example.com")
    for method, path in (
        ("GET", "/app/brain/share-links"),
        ("POST", "/app/brain/share-links"),
        ("POST", f"/app/brain/share-links/{uuid.uuid4()}/revoke"),
    ):
        assert anonymous.request(method, path, json={} if method == "POST" else None).status_code == 401
        assert unhired.request(method, path, json={} if method == "POST" else None).status_code == 403
