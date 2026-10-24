# ruff: noqa: F811
"""#433: how long reminders and approvals stay open are the owner's settings; the operator's value is the default."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from superaide.app.accounts import owner as hired_owner
from superaide.intentions import service as intentions
from superaide.platform import retention
from superaide.platform.config import get_settings
from superaide.platform.db.models import Approval, AuditLog, Intention
from superaide.platform.db.session import session_scope
from superaide.policy.binding import seal
from tests.integrations.gmail_fake import owner_client_fixture  # noqa: F401

URL = "/app/retention/read-settings"


def test_defaults_and_the_operators_value_when_the_owner_has_not_chosen(owner_client, monkeypatch):
    with session_scope() as s:
        assert retention.load(s) == {"reminder_auto_close_days": 7, "approval_ttl_hours": 0}
        assert retention.effective(s, "reminder_auto_close_days", 11) == 11  # the operator's, nothing chosen
    monkeypatch.setattr(get_settings(), "approval_ttl_hours", 5)
    a = Approval(kind="x", payload={"summary": "s"})
    seal(a)
    assert a.expires_at is not None  # still the operator's 5 hours


def test_the_owners_choice_wins_and_is_audited_and_validated(owner_client):
    assert owner_client.put(URL, json={"approval_ttl_hours": 5}).status_code == 422
    assert owner_client.put(URL, json={"approval_ttl_hours": 24, "reminder_auto_close_days": 3}).status_code == 200
    with session_scope() as s:
        assert retention.effective(s, "approval_ttl_hours", 0) == 24
        assert retention.effective(s, "reminder_auto_close_days", 7) == 3
        assert any(a.action == "retention_read_settings" for a in s.scalars(select(AuditLog)).all())
    before = datetime.now(UTC)
    a = Approval(kind="x", payload={"summary": "s"})
    seal(a)
    assert a.expires_at is not None and a.expires_at - before > timedelta(hours=23)


def test_reminders_that_went_off_are_closed_after_the_owners_days(owner_client):
    owner_client.put(URL, json={"reminder_auto_close_days": 3})
    now = datetime.now(UTC)
    with session_scope() as s:
        owner = hired_owner(s)
        for days in (2, 5):
            s.add(
                Intention(
                    owner_person_id=owner.id,
                    text=f"r{days}",
                    due_at=now - timedelta(days=days),
                    status="open",
                    delivered_at=now - timedelta(days=days),
                    source="chat",
                )
            )
    with session_scope() as s:
        assert intentions.auto_close_delivered(s) == 1  # only the one that went off 5 days ago
        done = {i.text for i in s.scalars(select(Intention).where(Intention.status == "done")).all()}
        assert done == {"r5"}
    owner_client.put(URL, json={"reminder_auto_close_days": 0})  # keep them open
    with session_scope() as s:
        assert intentions.auto_close_delivered(s) == 0
