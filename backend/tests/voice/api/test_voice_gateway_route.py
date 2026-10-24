"""Two-channel voice (#125): the web app is the text channel that confirms a call's proposal."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.identity.resolver import Sender
from superaide.platform.db.session import session_scope
from superaide.policy.actions import decide


@pytest.fixture
def on(monkeypatch, settings):
    monkeypatch.setattr(settings, "voice_provider", "fake")


def test_the_web_route_confirms_a_call_proposal_and_shows_the_readback(on, owner):
    with session_scope() as s:
        from superaide.platform.db.models import Person, UserAccount

        person = s.scalar(select(Person).join(UserAccount, UserAccount.person_id == Person.id))
        d = decide(
            s,
            kind="message_someone_else",
            summary="Tell Priya the meeting moved",
            requester=Sender(identity=None, person=person, trust=3),  # type: ignore[arg-type]
            origin="voice",
        )
        pending = str(d.approval.id)
    card = next(a for a in owner.get("/app/approvals").json() if a["id"] == pending)
    assert card["origin"] == "voice" and "Nothing has happened yet" in card["readback"]["spoken"]
    r = owner.post(f"/app/approvals/{pending}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
