"""Mandate shadow check (#112): each approval records whether a mandate WOULD have covered it, and the owner is
asked exactly as before. Off: nothing recorded. Never covers send or spend, a tainted turn, or an unstated limit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from superaide.config import get_settings
from superaide.db.models import Approval, Mandate, Person
from superaide.db.session import session_scope
from superaide.identity.resolver import Sender
from superaide.identity.trust import Trust
from superaide.mandate import service
from superaide.policy import actions
from superaide.policy.actions import TOOL_RULES

KEY = b"k" * 32


@pytest.fixture(autouse=True)
def keyed(monkeypatch):
    monkeypatch.setattr(service, "root_key", lambda: KEY)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(get_settings(), "mandates_enabled", True)
    monkeypatch.setattr(get_settings(), "mandate_shadow_enabled", True)


def sign(**over):
    base = dict(tools=["create_event", "move_event"], expires=datetime.now(UTC) + timedelta(days=2), max_uses=4)
    with session_scope() as s:
        row = service.create(s, uuid.uuid4(), **{**base, **over})
        return row.id


def ask(kind="calendar_create_event", payload=None, tainted=False, rules=TOOL_RULES):
    with session_scope() as s:
        p = Person(display_name="Jordan Whitfield", relation="manager")
        s.add(p)
        s.flush()
        sender = Sender(identity=None, person=p, trust=int(Trust.STEPPED_UP))
        d = actions.decide(s, kind=kind, summary="x", requester=sender, rules=rules, payload=payload, tainted=tainted)
        assert d.outcome == "approval"  # the owner is asked, whatever the shadow says
        a = d.approval
        s.expunge(a)
        return a


def test_off_nothing_is_recorded_even_with_a_matching_mandate():
    sign()
    assert "mandate_shadow" not in ask(payload={"event": {}, "notify": False}).payload


def test_needs_mandates_too(monkeypatch):
    monkeypatch.setattr(get_settings(), "mandate_shadow_enabled", True)  # but MANDATES_ENABLED is off
    assert "mandate_shadow" not in ask(payload={"event": {}, "notify": False}).payload


def test_a_covered_call_is_recorded_and_still_asks(on):
    mid = sign()
    a = ask(payload={"event": {}, "notify": False})
    assert a.status == "pending" and a.payload["mandate_shadow"] == {
        "would_cover": True,
        "effect": "write_external",
        "tainted": False,
    }
    with session_scope() as s:
        m = s.get(Mandate, mid)
        assert m is not None and m.uses == 0  # a measurement spends nothing


def test_no_mandate_or_other_tool_is_not_covered(on):
    assert ask(payload={"event": {}, "notify": False}).payload["mandate_shadow"]["would_cover"] is False
    sign(tools=["move_event"])
    assert ask(payload={"event": {}, "notify": False}).payload["mandate_shadow"]["would_cover"] is False


def test_inviting_other_people_is_a_send_and_never_covered(on):
    sign()
    seen = ask(payload={"event": {}, "notify": True}).payload["mandate_shadow"]
    assert seen["would_cover"] is False and seen["effect"] == "send"


def test_a_tainted_turn_is_never_covered(on):
    sign()
    seen = ask(payload={"event": {}, "notify": False}, tainted=True).payload["mandate_shadow"]
    assert seen["would_cover"] is False and seen["tainted"] is True


def test_a_limit_the_call_does_not_state_is_not_assumed_to_hold(on):
    sign(recipient_domains=["acme.com"])
    assert ask(payload={"event": {}, "notify": False}).payload["mandate_shadow"]["would_cover"] is False


def test_send_and_unknown_kinds_are_never_covered(on):
    sign(tools=["gmail_send", "publish_post", "calendar_decline_event"])
    rules = {**actions.RULES, "gmail_send": actions.Rule(int(Trust.LINKED), True, True)}
    for kind in ("gmail_send", "publish_post", "calendar_decline_event"):
        seen = ask(kind, {"x": 1}, rules={**rules, **TOOL_RULES}).payload["mandate_shadow"]
        assert seen["would_cover"] is False


def test_expired_and_revoked_mandates_are_not_covered(on):
    sign(expires=datetime.now(UTC) + timedelta(seconds=1))
    with session_scope() as s:
        s.query(Mandate).update({Mandate.expires_at: datetime.now(UTC) - timedelta(hours=1)})
    assert ask(payload={"event": {}, "notify": False}).payload["mandate_shadow"]["would_cover"] is False
    mid = sign()
    with session_scope() as s:
        service.revoke(s, mid)
    assert ask(payload={"event": {}, "notify": False}).payload["mandate_shadow"]["would_cover"] is False


def test_the_shadow_check_cannot_break_an_approval(on, monkeypatch):
    sign()
    monkeypatch.setattr(service, "permits", lambda *a, **k: 1 / 0)
    assert ask(payload={"event": {}, "notify": False}).payload["mandate_shadow"]["would_cover"] is False


def test_the_report_counts_what_a_mandate_would_have_saved(on):
    sign()
    ask(payload={"event": {}, "notify": False})
    ask(payload={"event": {}, "notify": True})
    with session_scope() as s:
        s.add(Approval(kind="gmail_send", payload={}))  # made before the check existed
        r = service.shadow_report(s)
    assert r == {"days": 30, "approvals": 3, "measured": 2, "would_cover": 1}


def test_the_calendar_tool_passes_whether_the_turn_was_tainted(on):
    from superaide.skills import calendar

    sign()
    with session_scope() as s:
        p = Person(display_name="Jordan Whitfield", relation="manager")
        s.add(p)
        s.flush()
        sender = Sender(identity=None, person=p, trust=int(Trust.STEPPED_UP))
        calendar._ask(s, sender, "calendar_create_event", "x", {"event": {}, "notify": False}, True)
        calendar._ask(s, sender, "calendar_create_event", "y", {"event": {}, "notify": False}, False)
        seen = {a.payload["summary"]: a.payload["mandate_shadow"]["would_cover"] for a in s.query(Approval)}
    assert seen == {"x": False, "y": True}
