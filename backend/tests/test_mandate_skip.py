"""Mandate skip (#112, MANDATE_SKIP_ENABLED): a covering signed mandate lets an own-calendar write run without a
card. Fake model, fake Google. Attacks: tainted turn, a guest added, expired or narrowed mandate, another tool,
the Infant stage, too little shadow data, used up, revoked."""

from __future__ import annotations

# ruff: noqa: F811  (pytest fixtures imported from test_calendar_actions)
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import Approval, AuditLog
from superaide.db.session import session_scope
from superaide.mandate import service
from superaide.policy.killswitch import set_state
from superaide.tenancy.context import use_instance
from tests.test_calendar_actions import (  # noqa: F401  (fixtures)
    _seed,
    app,
    fake,
    owner,
    pending,
    run,
    scope_of,
    switch_on,
)

EMAIL = "s@example.com"
KEY = b"k" * 32
GYM = {"title": "Gym", "when": "tomorrow at 7am"}


@pytest.fixture(autouse=True)
def keyed(monkeypatch):
    monkeypatch.setattr(service, "root_key", lambda: KEY)


def flags(monkeypatch, *, mandates=True, shadow=True, skip=True, days=14):
    s = get_settings()
    monkeypatch.setattr(s, "mandates_enabled", mandates)
    monkeypatch.setattr(s, "mandate_shadow_enabled", shadow)
    monkeypatch.setattr(s, "mandate_skip_enabled", skip)
    monkeypatch.setattr(s, "mandate_skip_min_days", days)


def stage(name):
    with use_instance(scope_of(EMAIL)), session_scope() as s:
        set_state(s, "stage", {"stage": name})


def sign(**over):
    base = dict(tools=["create_event", "move_event"], expires=datetime.now(UTC) + timedelta(days=2), max_uses=5)
    with use_instance(scope_of(EMAIL)), session_scope() as s:
        from superaide.db.models import Person  # noqa: F401

        return service.create(s, scope_of(EMAIL).agent_id, **{**base, **over}).id


def shadow_data(days=20):
    """An approval the shadow check recorded `days` ago."""
    with use_instance(scope_of(EMAIL)), session_scope() as s:
        a = Approval(kind="calendar_create_event", payload={"mandate_shadow": {"would_cover": True}}, status="rejected")
        a.created_at = datetime.now(UTC) - timedelta(days=days)
        s.add(a)


def skipped():
    with use_instance(scope_of(EMAIL)), session_scope() as s:
        return len(list(s.scalars(select(AuditLog).where(AuditLog.action == "mandate_skipped_card"))))


@pytest.fixture
def ready(owner, fake, monkeypatch):
    """Flags on, calendar actions on, Child stage, 20 days of shadow data."""
    flags(monkeypatch)
    switch_on(owner, fake)
    stage("child")
    shadow_data()
    return owner


def test_off_a_private_event_runs_as_before_and_asks_nothing(owner, fake, monkeypatch):
    flags(monkeypatch, skip=False)
    switch_on(owner, fake)
    sign()
    out, _ = run(EMAIL, "create_event", GYM)
    assert out.startswith("Added") and pending(EMAIL) == [] and skipped() == 0


def test_on_without_a_mandate_the_card_comes(ready, fake):
    out, _ = run(EMAIL, "create_event", GYM)
    assert "Waiting for the owner's approval" in out and fake.writes() == [] and len(pending(EMAIL)) == 1


def test_a_covering_mandate_skips_the_card_and_it_is_audited_and_shown(ready, fake):
    sign()
    out, err = run(EMAIL, "create_event", GYM)
    assert not err and out.startswith("Added") and pending(EMAIL) == [] and len(fake.writes()) == 1
    assert skipped() == 1
    listing = ready.get("/app/mandates")
    assert listing.status_code == 200
    body = listing.json()
    assert (
        body["skipping"] is True and body["skips"][0]["tool"] == "create_event" and "Gym" in body["skips"][0]["summary"]
    )
    assert body["mandates"][0]["uses"] == 1


def test_a_covering_mandate_moves_an_event_with_no_guests(ready, fake):
    sign()
    _seed(fake, "ev1", others=False)
    out, _ = run(EMAIL, "move_event", {"id": "ev1", "when": "Friday at 4pm"})
    assert out.startswith("Moved") and pending(EMAIL) == [] and skipped() == 1


def test_adult_stage_is_unchanged_and_uses_no_mandate(ready, fake):
    stage("adult")
    sign()
    out, _ = run(EMAIL, "create_event", GYM)
    assert out.startswith("Added") and skipped() == 0


# --- attacks ---------------------------------------------------------------------------------------------


def test_a_tainted_turn_still_asks(ready, fake):
    sign()
    out, _ = run(EMAIL, "create_event", GYM, tainted=True)
    assert "Waiting" in out and fake.writes() == [] and skipped() == 0


def test_a_guest_added_still_asks(ready, fake):
    sign()
    out, _ = run(EMAIL, "create_event", {**GYM, "attendees": ["p@example.com"]})
    assert "Waiting" in out and fake.writes() == [] and skipped() == 0


def test_moving_a_meeting_with_others_still_asks(ready, fake):
    sign()
    _seed(fake, "ev2", others=True)
    out, _ = run(EMAIL, "move_event", {"id": "ev2", "when": "Friday at 4pm"})
    assert "Waiting" in out and fake.writes() == [] and skipped() == 0


def test_the_infant_stage_ignores_mandates(ready, fake):
    stage("infant")
    sign()
    out, _ = run(EMAIL, "create_event", GYM)
    assert "Waiting" in out and fake.writes() == [] and skipped() == 0


def test_too_little_shadow_data_still_asks(owner, fake, monkeypatch):
    flags(monkeypatch)
    switch_on(owner, fake)
    stage("child")
    sign()
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0]  # no shadow data at all
    shadow_data(days=3)
    assert "Waiting" in run(EMAIL, "create_event", {**GYM, "title": "Gym 2"})[0]
    assert fake.writes() == [] and skipped() == 0


def test_the_minimum_days_is_configurable(owner, fake, monkeypatch):
    flags(monkeypatch, days=2)
    switch_on(owner, fake)
    stage("child")
    sign()
    shadow_data(days=3)
    assert run(EMAIL, "create_event", GYM)[0].startswith("Added")


def test_shadow_off_means_no_data_and_no_skip(owner, fake, monkeypatch):
    flags(monkeypatch, shadow=False)
    switch_on(owner, fake)
    stage("child")
    sign()
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0]
    assert all("mandate_shadow" not in a.payload for a in pending(EMAIL))


def test_mandates_off_overrides_skip(owner, fake, monkeypatch):
    flags(monkeypatch, mandates=False)
    switch_on(owner, fake)
    stage("child")
    shadow_data()
    assert run(EMAIL, "create_event", GYM)[0].startswith("Added")  # skip is inert: the classic write_own path


def test_an_expired_mandate_still_asks(ready, fake):
    sign(expires=datetime.now(UTC) + timedelta(seconds=1))
    with use_instance(scope_of(EMAIL)), session_scope() as s:
        from superaide.db.models import Mandate

        for m in s.scalars(select(Mandate)):
            m.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0] and skipped() == 0


def test_a_mandate_for_another_tool_still_asks(ready, fake):
    sign(tools=["gmail_draft_reply"])
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0] and skipped() == 0


def test_a_narrowed_mandate_cannot_borrow_the_parents_reach(ready, fake):
    parent = sign(tools=["create_event", "move_event"])
    with use_instance(scope_of(EMAIL)), session_scope() as s:
        service.delegate(s, parent, label="helper", tools=["move_event"])
        from superaide.db.models import Mandate

        row = s.get(Mandate, parent)
        row.status = "revoked"  # the owner revokes the parent: the child goes with the chain
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0] and skipped() == 0


def test_a_used_up_mandate_asks_again(ready, fake):
    sign(max_uses=1)
    assert run(EMAIL, "create_event", GYM)[0].startswith("Added")
    assert "Waiting" in run(EMAIL, "create_event", {**GYM, "title": "Gym 2"})[0]
    assert skipped() == 1


def test_a_revoked_mandate_asks_again(ready, fake):
    mid = sign()
    with use_instance(scope_of(EMAIL)), session_scope() as s:
        service.revoke(s, mid)
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0] and skipped() == 0


def test_a_mandate_that_names_send_tools_never_skips_a_decline(ready, fake):
    sign(tools=["create_event", "move_event", "decline_event"])
    _seed(fake, "ev3", others=True, mine=False)
    out, _ = run(EMAIL, "decline_event", {"id": "ev3"})
    assert "Waiting" in out and skipped() == 0


# --- narrowed mandates are checked against the real call, never waved through ------------------------------------


def test_a_mandate_limited_by_money_or_topic_never_skips_a_call_that_does_not_state_them(ready, fake):
    sign(max_amount=10)
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0] and skipped() == 0


def test_a_mandate_limited_by_topic_never_skips(ready, fake):
    sign(topics=["gym"])
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0] and skipped() == 0


def test_a_recipient_limited_mandate_still_covers_a_call_that_tells_nobody(ready, fake):
    sign(recipient_domains=["example.com"])
    assert run(EMAIL, "create_event", GYM)[0].startswith("Added") and skipped() == 1


def test_a_recipient_limited_mandate_never_covers_a_guest_outside_it(ready, fake):
    sign(recipient_domains=["example.com"])
    out, _ = run(EMAIL, "create_event", {**GYM, "attendees": ["x@outside.org"]})
    assert "Waiting" in out and skipped() == 0 and fake.writes() == []


def test_a_child_that_narrowed_the_tools_cannot_create_events(ready, fake):
    parent = sign()
    with use_instance(scope_of(EMAIL)), session_scope() as s:
        service.delegate(s, parent, label="move only", tools=["move_event"])
        from superaide.db.models import Mandate

        s.get(Mandate, parent).status = "revoked"  # only the child could cover it, and it names move_event alone
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0] and skipped() == 0


def test_the_unknown_narrowing_key_fails_closed():
    from superaide.mandate import token as tk

    tok = tk.issue(
        KEY,
        agent_id="a",
        issuer=None,
        tools=["create_event"],
        expires=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    assert not tk.authorize(
        KEY, tk.narrow(tok, "max_uses", 1), agent_id="a", tool="create_event", args={}, now=datetime.now(UTC), uses=1
    ).ok


# --- flag on, NO mandate: the same as main everywhere but the two stages that ask ---------------------------------


def test_flag_on_without_a_mandate_nothing_is_relaxed_at_any_stage(owner, fake, monkeypatch):
    """Compared with main (flag off): a tainted turn and an invite still ask at every stage; no stage asks LESS."""
    flags(monkeypatch)
    switch_on(owner, fake)
    shadow_data()
    for st in ("infant", "child", "adolescent", "adult"):
        stage(st)
        tainted, _ = run(EMAIL, "create_event", {**GYM, "title": f"t-{st}"}, tainted=True)
        assert "Waiting" in tainted, st
        invite, _ = run(EMAIL, "create_event", {**GYM, "title": f"i-{st}", "attendees": ["p@example.com"]})
        assert ("Waiting" in invite) == (st in ("infant", "child")), st  # as on main
    assert len(fake.writes()) == 2  # only the two invites at Adolescent and Adult ran, exactly as on main


def test_flag_on_without_a_mandate_higher_stages_run_a_private_event_as_on_main(owner, fake, monkeypatch):
    flags(monkeypatch)
    switch_on(owner, fake)
    for st in ("adolescent", "adult"):
        stage(st)
        assert run(EMAIL, "create_event", {**GYM, "title": st})[0].startswith("Added")
    assert pending(EMAIL) == [] and skipped() == 0


def test_flag_on_without_a_mandate_infant_and_child_ask_for_a_private_event_by_design(owner, fake, monkeypatch):
    """The one deliberate difference from main: an own-calendar write is write_external while the flag is on."""
    flags(monkeypatch)
    switch_on(owner, fake)
    for st in ("infant", "child"):
        stage(st)
        assert "Waiting" in run(EMAIL, "create_event", {**GYM, "title": st})[0]


def test_no_skip_ever_happens_on_a_tainted_turn_even_with_every_condition_met(ready, fake):
    sign()
    for _ in range(3):
        assert "Waiting" in run(EMAIL, "create_event", GYM, tainted=True)[0]
    assert skipped() == 0 and fake.writes() == []
