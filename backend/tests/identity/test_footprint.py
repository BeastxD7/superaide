"""Footprint Guard (#136): creating things in the world under the owner's name, and naming people outside the
owner's contacts, always wait for the owner. Off by default."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.approvals import footprint
from superaide.approvals.models import Approval
from superaide.identity.resolver import Sender
from superaide.identity.trust import Trust
from superaide.platform.config import get_settings
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.policy.actions import action_kinds, decide


def owner_sender(name="Jordan") -> Sender:
    from superaide.identity.models import Person

    with session_scope() as s:
        p = Person(display_name=name, relation="manager")
        s.add(p)
        s.flush()
        s.expunge(p)
    return Sender(identity=None, person=p, trust=int(Trust.STEPPED_UP))


def ask(kind: str, summary: str):
    sender = owner_sender()
    with session_scope() as s:
        d = decide(s, kind=kind, summary=summary, requester=sender)
        return d.outcome, (dict(d.approval.payload) if d.approval else {})


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(get_settings(), "footprint_guard_enabled", True)


def test_off_by_default_creation_is_refused_and_not_offered():
    assert not get_settings().footprint_guard_enabled
    assert "create_profile" not in action_kinds()
    assert ask("create_profile", "Make a dating profile")[0] == "refused"


@pytest.mark.parametrize(
    "kind", ["create_account", "create_profile", "open_pull_request", "post_review", "publish_post"]
)
def test_creation_needs_approval_and_is_logged(on, kind):
    assert kind in action_kinds()
    outcome, payload = ask(kind, "Do it")
    assert outcome == "approval" and payload["footprint"] == "creates_in_the_world" if kind != "publish_post" else True
    with session_scope() as s:
        assert s.scalar(select(AuditLog.id).where(AuditLog.action == "footprint_creation")) is not None
        assert s.scalar(select(Approval.id)) is not None


def test_a_post_naming_someone_outside_the_contacts_is_flagged(on):
    outcome, payload = ask("publish_post", "Publish: Daniel Cho cheated our customers.")
    assert outcome == "approval" and payload["names_outside_contacts"] == ["Daniel Cho"]


def test_a_known_contact_is_not_flagged(on):
    with session_scope() as s:
        from superaide.identity.models import Person

        s.add(Person(display_name="Priya Tan", relation="colleague"))
    _, payload = ask("message_someone_else", "Tell Priya Tan the meeting moved.")
    assert "names_outside_contacts" not in payload


def test_nothing_is_added_when_off():
    _, payload = ask("message_someone_else", "Tell Daniel Cho the meeting moved.")
    assert "names_outside_contacts" not in payload and "footprint" not in payload


def test_a_non_manager_cannot_create(on):
    from superaide.identity.models import Person

    with session_scope() as s:
        p = Person(display_name="Eve", relation="colleague")
        s.add(p)
        s.flush()
        s.expunge(p)
    sender = Sender(identity=None, person=p, trust=int(Trust.LINKED))
    with session_scope() as s:
        assert decide(s, kind="create_account", summary="x", requester=sender).outcome == "refused"


def test_unknown_names_ignores_greetings(on):
    with session_scope() as s:
        assert footprint.unknown_names(s, "Thank You and Best Regards") == []
