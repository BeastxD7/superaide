"""#448: mandate shadow and skip ship ON. A fresh install runs the shadow clock; nothing is skipped without a signed
mandate, and every safeguard (untainted turn, Child stage or above, enough shadow data, in-scope mandate) still holds
under the defaults. The owner-accepted side effect: a no-guest calendar event asks at Infant and Child."""

from __future__ import annotations

# ruff: noqa: F811  (pytest fixtures imported from test_calendar_actions)
import pytest

from superaide.platform.config import Settings, get_settings
from tests.integrations.test_calendar_actions import app, fake, owner, pending, run, switch_on  # noqa: F401
from tests.mandate.test_mandate_skip import EMAIL, GYM, keyed, shadow_data, sign, skipped, stage  # noqa: F401

FLAGS = ("MANDATES_ENABLED", "MANDATE_SHADOW_ENABLED", "MANDATE_SKIP_ENABLED", "MANDATE_SKIP_MIN_DAYS")


def fresh_settings(monkeypatch) -> Settings:
    for name in FLAGS:
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None)


@pytest.fixture
def defaults(owner, fake, monkeypatch):
    """The settings of a fresh install (no mandate flags in the environment), calendar actions on."""
    fresh = fresh_settings(monkeypatch)
    live = get_settings()
    for name in ("mandates_enabled", "mandate_shadow_enabled", "mandate_skip_enabled", "mandate_skip_min_days"):
        monkeypatch.setattr(live, name, getattr(fresh, name))
    switch_on(owner, fake)
    return owner


def test_a_fresh_install_has_shadow_and_skip_on(monkeypatch):
    s = fresh_settings(monkeypatch)
    assert (s.mandates_enabled, s.mandate_shadow_enabled, s.mandate_skip_enabled) == (True, True, True)
    assert s.mandate_skip_min_days == 14


def test_each_flag_can_still_be_turned_off(monkeypatch):
    for name in FLAGS[:3]:
        monkeypatch.setenv(name, "false")
        assert getattr(Settings(_env_file=None), name.lower()) is False
        monkeypatch.delenv(name)


def test_by_default_without_a_mandate_nothing_is_ever_skipped(defaults, fake):
    shadow_data()
    for st in ("infant", "child", "adolescent", "adult"):
        stage(st)
        run(EMAIL, "create_event", {**GYM, "title": st}, tainted=True)
    assert skipped() == 0


def test_by_default_a_no_guest_event_asks_at_infant_and_child_with_no_mandate(defaults, fake):
    """The owner-accepted side effect of #448: an own-calendar write is write_external while skip is on."""
    for st in ("infant", "child"):
        stage(st)
        out, _ = run(EMAIL, "create_event", {**GYM, "title": st})
        assert "Waiting" in out, st
    assert fake.writes() == [] and len(pending(EMAIL)) == 2 and skipped() == 0


def test_by_default_later_stages_without_a_mandate_run_as_before(defaults, fake):
    for st in ("adolescent", "adult"):
        stage(st)
        assert run(EMAIL, "create_event", {**GYM, "title": st})[0].startswith("Added")
    assert pending(EMAIL) == [] and skipped() == 0


def test_by_default_a_fresh_install_has_no_shadow_data_so_even_a_mandate_asks(defaults, fake):
    stage("child")
    sign()
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0]
    assert skipped() == 0 and fake.writes() == []


def test_by_default_the_safeguards_hold_with_a_covering_mandate(defaults, fake):
    shadow_data()
    sign()
    stage("infant")
    assert "Waiting" in run(EMAIL, "create_event", GYM)[0]  # never at Infant
    stage("child")
    assert "Waiting" in run(EMAIL, "create_event", GYM, tainted=True)[0]  # never on a tainted turn
    assert "Waiting" in run(EMAIL, "create_event", {**GYM, "attendees": ["p@example.com"]})[0]  # never with a guest
    assert skipped() == 0 and fake.writes() == []
    out, _ = run(EMAIL, "create_event", GYM)  # every condition met: the one case that skips, and it is audited
    assert out.startswith("Added") and skipped() == 1
