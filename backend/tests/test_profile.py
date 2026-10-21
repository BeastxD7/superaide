"""The owner brief in every prompt: full for the owner, only what's allowed for anyone else."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app.accounts import owner
from superaide.db.models import ChannelIdentity, Person
from superaide.db.session import session_scope
from superaide.identity.trust import Trust
from superaide.knowledge.store import add_contact, preferences_for
from superaide.main import create_app
from tests.conftest import sl, tg


def _setup(agent):
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": "correct horse battery"})
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    with session_scope() as s:
        me = owner(s)
        pref = preferences_for(s, me)
        pref.urgent_topics = ["A family emergency", "Anything about the Series A"]
        pref.shareable = ["availability"]
        pref.never_share = "my home address"
        priya = add_contact(s, owner=me, name="Priya", relationship="partner", vip=True)
        add_contact(s, owner=me, name="Arjun", relationship="team", vip=False)
        # Priya's Telegram is linked to her record, so the agent knows it's her.
        s.add(
            ChannelIdentity(
                person_id=priya.person_id, provider="telegram", tenant_scope="", stable_key="5550001",
                trust_level=int(Trust.LINKED), verification={"method": "test"},
            )
        )  # fmt: skip
    return c


def test_the_owner_sees_their_standing_rules(agent, model):
    c = _setup(agent)
    c.post("/app/chat", json={"text": "What do you know about my week?"})
    system = model.systems[-1]
    assert "You work for Shashank Rao" in system
    assert "Anything about the Series A" in system and "Priya (partner, always gets through)" in system
    assert "Never share: my home address" in system


def test_others_only_get_what_the_owner_allowed(agent, model, colleague):
    _setup(agent)
    agent.handle(sl("U_PRIYA", "Is Shashank around? Where does he live?"))  # a colleague on Slack
    system = model.systems[-1]
    assert "You may tell this person: whether they're free or busy (no details)" in system
    assert "Everything else about your owner is private" in system
    # Private settings, people and even the description of the secret stay private.
    assert "Series A" not in system and "Arjun" not in system and "partner" not in system
    assert "home address" not in system


def test_a_vip_contact_is_recognised_as_urgent(agent, model):
    _setup(agent)
    with session_scope() as s:
        assert s.scalar(select(Person.relation).where(Person.display_name == "Priya")) == "contact"
    agent.handle(tg("5550001", "Can you ask Shashank to call me?"))
    system = model.systems[-1]
    assert "This person is your owner's partner, and can always get through" in system
    assert "Series A" not in system


def _fact(s, person, content, category="preference", allowed=None, valid_to=None):
    from superaide.db.models import MemoryItem

    s.add(
        MemoryItem(
            content=content, subject_person_ids=[person.id], allowed_kind="persons",
            allowed_person_ids=allowed or [person.id], min_trust=2, category=category, valid_to=valid_to,
        )
    )  # fmt: skip


def test_learned_preferences_are_pinned_in_the_owners_own_brief_only(agent, model, colleague):
    from datetime import UTC, datetime

    c = _setup(agent)
    with session_scope() as s:
        me = owner(s)
        _fact(s, me, "Shashank does not like the colour black.")
        _fact(s, me, "Shashank is vegetarian.", category="about")
        _fact(s, me, "Shashank used to like grey.", valid_to=datetime.now(UTC))  # retired: not pinned
        _fact(s, me, "Shashank's flight is on Friday.", category="work")  # not a core category
        _fact(s, me, "Asked about the week, Shashank said: " + "x" * 300, category="about")  # too long
    c.post("/app/chat", json={"text": "hello"})
    system = model.systems[-1]
    assert "What you know about them" in system
    assert system.index("does not like the colour black") < system.index("is vegetarian")  # preferences first
    for absent in ("used to like grey", "flight is on Friday", "x" * 300):
        assert absent not in system
    # A colleague never gets them (groups: see test_cross_channel.py).
    n = len(model.systems)
    agent.handle(sl("U_PRIYA", "What colours does Shashank like?"))
    assert len(model.systems) == n + 1
    assert "colour black" not in model.systems[-1] and "What you know about them" not in model.systems[-1]


def test_pinned_facts_stay_within_their_budget(agent, model):
    from superaide.knowledge.profile import PINNED_CHARS, PINNED_MAX

    c = _setup(agent)
    with session_scope() as s:
        me = owner(s)
        for i in range(40):
            _fact(s, me, f"Shashank preference number {i}: " + "y" * 150)
    c.post("/app/chat", json={"text": "hello"})
    pinned = [line for line in model.systems[-1].splitlines() if "preference number" in line]
    assert 0 < len(pinned) <= PINNED_MAX and sum(len(p) for p in pinned) <= PINNED_CHARS + 2 * len(pinned)
