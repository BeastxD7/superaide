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
    assert "Never share, whatever the reason: my home address" in system
    # Private settings and people stay private.
    assert "Series A" not in system and "Arjun" not in system and "partner" not in system


def test_a_vip_contact_is_recognised_as_urgent(agent, model):
    _setup(agent)
    with session_scope() as s:
        assert s.scalar(select(Person.relation).where(Person.display_name == "Priya")) == "contact"
    agent.handle(tg("5550001", "Can you ask Shashank to call me?"))
    system = model.systems[-1]
    assert "This person is your owner's partner, and can always get through" in system
    assert "Series A" not in system
