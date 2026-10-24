"""The Glass brain API (/app/brain): a map of what Ava knows, through the audience gate, as the owner."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.knowledge.store import add_contact, remember_fact
from superaide.platform.config import get_settings
from superaide.platform.db.models import Document, MemoryItem, Person
from superaide.platform.db.session import session_scope
from tests.api_support import OTHER, OWNER, hire, signup
from tests.tenancy.api.test_many_owners import account


@pytest.fixture(autouse=True)
def many_owners_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_many_owners", True)


def owners(browser):
    a, b = browser(), browser()
    assert signup(a).status_code == 200 and hire(a).status_code == 200
    assert signup(b, **OTHER).status_code == 200 and hire(b).status_code == 200
    return a, b


def seed() -> dict[str, str]:
    owner_id = account(OWNER["email"]).person_id
    with session_scope() as s:
        owner = s.get(Person, owner_id)
        assert owner is not None
        chat = remember_fact(s, owner=owner, content="Jordan prefers calls before 11am.", category="preference")
        doc = Document(
            owner_person_id=owner_id, kind="pdf", title="resume.pdf", source="resume.pdf", sha256="b" * 64, text="x"
        )
        s.add(doc)
        s.flush()
        filed = remember_fact(
            s, owner=owner, content="Jordan led Project Bluebird with Arjun.", category="work", source="document",
            document=doc,
        )  # fmt: skip
        old = remember_fact(s, owner=owner, content="Jordan lives in Pune.", category="about")
        assert chat and filed and old
        old.created_at = datetime.now(UTC) - timedelta(days=400)
        add_contact(s, owner=owner, name="Arjun Mehta", relationship="team")
        # Rows that must never appear on this owner's map:
        stranger = Person(display_name="Priya Tan", relation="contact")
        s.add(stranger)
        s.flush()
        remember_fact(s, owner=stranger, content="Priya plans a surprise party.", category="about")
        hidden = MemoryItem(  # about the owner, but only Priya may see it
            content="Jordan is being considered for a board seat.",
            subject_person_ids=[owner_id],
            allowed_kind="persons",
            allowed_person_ids=[stranger.id],
        )
        s.add(hidden)
        return {"chat": str(chat.id), "filed": str(filed.id), "old": str(old.id)}


def labels(map_: dict, kind: str) -> list[str]:
    return [n["label"] for n in map_["nodes"] if n["type"] == kind and not n.get("superseded")]


def test_the_map_has_people_topics_facts_and_where_they_came_from(browser):
    a, _ = owners(browser)
    ids = seed()
    m = a.get("/app/brain").json()
    assert set(labels(m, "fact")) == {
        "Jordan prefers calls before 11am.",
        "Jordan led Project Bluebird with Arjun.",
        "Jordan lives in Pune.",
    }
    assert set(labels(m, "topic")) == {"preference", "work", "about"}
    assert labels(m, "person") == ["Arjun Mehta"] and labels(m, "source") == ["resume.pdf"]
    by_id = {n["id"]: n for n in m["nodes"]}
    filed = by_id[f"fact:{ids['filed']}"]
    assert filed["origin"] == "file" and filed["document"] == "resume.pdf"
    assert by_id[f"fact:{ids['chat']}"]["origin"] == "you"
    assert by_id[f"fact:{ids['old']}"]["freshness"] == 0.35 and by_id[f"fact:{ids['chat']}"]["freshness"] > 0.95
    kinds = {(e["from"][:5], e["to"][:5], e["kind"]) for e in m["edges"]}
    assert ("sourc", "fact:", "learned_from") in kinds and ("topic", "fact:", "about") in kinds
    assert ("fact:", "perso", "mentions") in kinds  # "Arjun" appears in a fact
    assert m["origins"]["file"]["count"] == 1 and m["origins"]["you"]["count"] == 2


def test_a_fact_the_owner_may_not_see_is_never_rendered(browser):
    """Canary: facts about others, and facts about the owner that only someone else may see, are not in the
    response at all, in any field."""
    a, b = owners(browser)
    seed()
    text = a.get("/app/brain").text
    for secret in ("surprise party", "board seat", "Priya"):
        assert secret not in text, secret
    assert "Jordan" not in b.get("/app/brain").text  # and the other owner sees none of it


def test_forgetting_moves_a_fact_to_the_forgotten_shelf(browser):
    a, _ = owners(browser)
    ids = seed()
    assert a.post(f"/app/notebook/{ids['chat']}/forget").status_code == 200
    m = a.get("/app/brain").json()
    assert "Jordan prefers calls before 11am." not in labels(m, "fact")
    assert [f["text"] for f in m["forgotten"]] == ["Jordan prefers calls before 11am."]


def test_correcting_retires_the_old_fact_and_records_the_owners_words(browser):
    a, _ = owners(browser)
    ids = seed()
    r = a.post(f"/app/brain/facts/{ids['old']}/correct", json={"text": "Jordan lives in Singapore."})
    assert r.status_code == 200
    m = a.get("/app/brain").json()
    assert "Jordan lives in Pune." not in labels(m, "fact") and "Jordan lives in Singapore." in labels(m, "fact")
    assert [f["text"] for f in m["forgotten"]] == ["Jordan lives in Pune."]
    with session_scope() as s:
        new = s.get(MemoryItem, uuid.UUID(r.json()["id"]))
        assert new is not None and new.source == "owner_edit" and new.confidence == 1.0 and new.category == "about"
    assert a.post(f"/app/brain/facts/{ids['old']}/correct", json={"text": "again"}).status_code == 404


def test_correcting_checks_ownership_and_input(browser):
    a, b = owners(browser)
    ids = seed()
    assert b.post(f"/app/brain/facts/{ids['old']}/correct", json={"text": "Hacked"}).status_code == 404
    assert a.post(f"/app/brain/facts/{ids['old']}/correct", json={"text": "   "}).status_code == 422
    assert a.post(f"/app/brain/facts/{ids['old']}/correct", json={"text": "x" * 2001}).status_code == 422
    assert a.post(f"/app/brain/facts/{uuid.uuid4()}/correct", json={"text": "x"}).status_code == 404
    assert a.post("/app/brain/facts/nope/correct", json={"text": "x"}).status_code == 422
    with session_scope() as s:
        assert s.scalar(select(MemoryItem).where(MemoryItem.content == "Hacked")) is None
        assert s.get(MemoryItem, uuid.UUID(ids["old"])).valid_to is None  # type: ignore[union-attr]


def test_the_share_picture_has_no_names_or_text(browser):
    a, _ = owners(browser)
    seed()
    r = a.get("/app/brain/share.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    for private in ("Jordan", "Bluebird", "Arjun", "Pune", "resume", "11am", "Priya", "surprise"):
        assert private not in r.text, private
    assert "<circle" in r.text and "things Ava knows" in r.text


def test_the_brain_routes_need_the_owner(browser):
    anonymous = browser()
    unhired = browser()
    signup(unhired, name="Mallory Tan", email="mallory@example.com")
    for method, path in (
        ("GET", "/app/brain"),
        ("GET", "/app/brain/share.svg"),
        ("POST", f"/app/brain/facts/{uuid.uuid4()}/correct"),
        ("POST", f"/app/brain/facts/{uuid.uuid4()}/lock"),
        ("POST", f"/app/brain/facts/{uuid.uuid4()}/unlock"),
    ):
        assert anonymous.request(method, path, json={"text": "x"} if method == "POST" else None).status_code == 401
        assert unhired.request(method, path, json={"text": "x"} if method == "POST" else None).status_code == 403


def test_a_corrected_fact_stays_on_the_map_struck_through_and_points_at_its_replacement(browser):
    a, _ = owners(browser)
    ids = seed()
    new_id = a.post(f"/app/brain/facts/{ids['old']}/correct", json={"text": "Jordan lives in Singapore."}).json()["id"]
    m = a.get("/app/brain").json()
    by_id = {n["id"]: n for n in m["nodes"]}
    old, new = by_id[f"fact:{ids['old']}"], by_id[f"fact:{new_id}"]
    assert old["superseded"] is True and old["label"] == "Jordan lives in Pune."
    assert old["replaced_by"] == f"fact:{new_id}" and "superseded" not in new
    assert {"from": f"fact:{ids['old']}", "to": f"fact:{new_id}", "kind": "replaced_by"} in m["edges"]
    # The live facts are unchanged: the struck-through one is not counted as something Ava believes.
    assert m["origins"]["you"]["count"] == 2 and "Jordan lives in Pune." in [f["text"] for f in m["forgotten"]]


def test_forgetting_a_fact_is_not_a_replacement_and_adds_no_strike_through(browser):
    a, _ = owners(browser)
    ids = seed()
    a.post(f"/app/notebook/{ids['chat']}/forget")
    assert not [n for n in a.get("/app/brain").json()["nodes"] if n.get("superseded")]


def test_a_retired_fact_the_owner_may_not_see_is_never_on_the_shelf_or_struck_through(browser):
    """Canary: retired notes pass the audience gate too. A note about the owner that only Priya may see, once
    replaced, appears nowhere in the response; neither does a note replaced by something the owner may not see."""
    a, _ = owners(browser)
    seed()
    owner_id = account(OWNER["email"]).person_id
    with session_scope() as s:
        priya = Person(display_name="Priya Tan", relation="contact")
        s.add(priya)
        s.flush()
        for text in ("Board seat, the old plan.", "Board seat, the new plan."):
            s.add(
                MemoryItem(
                    content=text, subject_person_ids=[owner_id], allowed_kind="persons", allowed_person_ids=[priya.id]
                )
            )
        s.flush()
        old, new = (
            s.scalar(select(MemoryItem).where(MemoryItem.content == t))
            for t in ("Board seat, the old plan.", "Board seat, the new plan.")
        )
        assert old and new
        old.valid_to, old.superseded_by = datetime.now(UTC) - timedelta(days=1), new.id
        # And an owner-visible note replaced by a successor nobody but Priya may see:
        mine = remember_fact(
            s, owner=s.get(Person, owner_id), content="Visible but replaced elsewhere.", category="about"
        )  # type: ignore[arg-type]
        assert mine is not None
        mine.valid_to, mine.superseded_by = datetime.now(UTC) - timedelta(days=1), new.id
    text = a.get("/app/brain").text
    assert "Board seat" not in text
    assert "Visible but replaced elsewhere." in text  # forgotten shelf (owner may see it) but never struck through
    assert not [n for n in a.get("/app/brain").json()["nodes"] if n.get("superseded")]


def test_locking_a_fact_pins_it_and_unlocking_puts_it_back(browser):
    a, b = owners(browser)
    ids = seed()
    nodes = lambda: {n["id"]: n for n in a.get("/app/brain").json()["nodes"]}  # noqa: E731
    assert nodes()[f"fact:{ids['old']}"]["locked"] is False and nodes()[f"fact:{ids['old']}"]["freshness"] == 0.35
    r = a.post(f"/app/brain/facts/{ids['old']}/lock")
    assert r.status_code == 200 and r.json() == {"id": ids["old"], "locked": True}
    pinned = nodes()[f"fact:{ids['old']}"]
    assert pinned["locked"] is True and pinned["freshness"] == 1.0  # a locked note does not fade
    assert a.post(f"/app/brain/facts/{ids['old']}/lock").status_code == 200  # idempotent
    with session_scope() as s:
        assert s.get(MemoryItem, uuid.UUID(ids["old"])).locked is True  # type: ignore[union-attr]
    assert b.post(f"/app/brain/facts/{ids['old']}/unlock").status_code == 404  # not their note
    assert a.post(f"/app/brain/facts/{ids['old']}/unlock").json()["locked"] is False
    assert nodes()[f"fact:{ids['old']}"]["freshness"] == 0.35


def test_lock_refuses_other_peoples_retired_and_unseen_notes(browser):
    a, _ = owners(browser)
    ids = seed()
    assert a.post(f"/app/brain/facts/{uuid.uuid4()}/lock").status_code == 404
    a.post(f"/app/notebook/{ids['chat']}/forget")
    assert a.post(f"/app/brain/facts/{ids['chat']}/lock").status_code == 404  # already forgotten
    with session_scope() as s:
        hidden = s.scalar(select(MemoryItem).where(MemoryItem.content.contains("board seat")))
        assert hidden is not None
        hidden_id = str(hidden.id)
    assert a.post(f"/app/brain/facts/{hidden_id}/lock").status_code == 404  # about the owner, but they may not see it
    with session_scope() as s:
        assert s.get(MemoryItem, uuid.UUID(hidden_id)).locked is False  # type: ignore[union-attr]


def test_a_locked_fact_can_still_be_corrected_by_the_owner(browser):
    a, _ = owners(browser)
    ids = seed()
    a.post(f"/app/brain/facts/{ids['old']}/lock")
    assert a.post(f"/app/brain/facts/{ids['old']}/correct", json={"text": "Jordan lives in Goa."}).status_code == 200
