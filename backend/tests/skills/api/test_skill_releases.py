"""Blast-radius cooldown (#129): a new skill version waits by effect class; retired names are never reissued."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from superaide.config import get_settings
from superaide.db.session import session_scope
from superaide.skills import cooldown, registry
from tests.api_support import signup

NOW = datetime.now(UTC)
KEY = "reviewer-key-for-tests"


@pytest.fixture(autouse=True)
def flag_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "skill_cooldown_enabled", True)
    monkeypatch.setattr(get_settings(), "skill_reviewer_key", KEY)


def test_wait_depends_on_the_strongest_effect():
    assert cooldown.effect_class(["read_private", "write_own"]) == "write_own"
    assert cooldown.delay("read_public") == timedelta(0)
    assert cooldown.delay("write_own") == timedelta(hours=24)
    for effect in ("write_external", "send", "spend"):
        assert cooldown.delay(effect) == timedelta(hours=72)
    assert cooldown.delay("send", "reviewed") == timedelta(hours=36)  # an earned tier halves it, nothing else does


def test_a_fake_malicious_update_does_not_reach_an_installer_before_the_delay():
    skill_id = "superaide/reminders"  # a real catalog skill
    version = registry.catalog()[skill_id].manifest.version
    with session_scope() as s:
        cooldown.record_release(s, skill_id, version, ["send"], now=NOW - timedelta(hours=71))
        with pytest.raises(registry.SkillUnavailable) as exc:
            registry.install(s, skill_id, registry.catalog()[skill_id].manifest.permissions)
        assert exc.value.status == 409 and "becomes available" in str(exc.value)
        assert cooldown.latest_installable(s, skill_id, NOW) is None
        assert cooldown.latest_installable(s, skill_id, NOW + timedelta(hours=2)) is not None


def test_the_clock_never_restarts_and_read_only_versions_are_not_held():
    with session_scope() as s:
        first = cooldown.record_release(s, "acme/notes", "1.0.0", ["write_own"], now=NOW - timedelta(hours=30))
        again = cooldown.record_release(s, "acme/notes", "1.0.0", ["write_own"], now=NOW)
        assert again.id == first.id and cooldown.is_available(again, NOW)
        read = cooldown.record_release(s, "acme/notes", "1.0.1", ["read_private"], now=NOW)
        assert cooldown.is_available(read, NOW)


def test_only_a_valid_reviewer_signature_bypasses(monkeypatch):
    with session_scope() as s:
        with pytest.raises(cooldown.CooldownError):
            cooldown.record_release(s, "acme/notes", "2.0.0", ["send"], patch_signature="f" * 64)
        sig = cooldown.sign_patch(KEY, "acme/notes", "2.0.1")
        patch = cooldown.record_release(s, "acme/notes", "2.0.1", ["send"], patch_signature=sig, now=NOW)
        assert cooldown.is_available(patch, NOW)
        monkeypatch.setattr(get_settings(), "skill_reviewer_key", "")  # no key: no bypass exists
        assert not cooldown.is_available(patch, NOW)


def test_a_retired_name_is_reserved_for_good():
    with session_scope() as s:
        cooldown.retire_name(s, "acme/gone", "withdrawn")
        cooldown.retire_name(s, "acme/gone")  # twice is fine
        with pytest.raises(cooldown.CooldownError) as exc:
            cooldown.record_release(s, "acme/gone", "1.0.0", ["read_public"])
        assert exc.value.status == 410


def test_with_the_flag_off_nothing_is_held_back(monkeypatch):
    skill_id = "superaide/reminders"
    skill = registry.catalog()[skill_id]
    with session_scope() as s:
        cooldown.record_release(s, skill_id, skill.manifest.version, ["send"], now=NOW)
        monkeypatch.setattr(get_settings(), "skill_cooldown_enabled", False)
        registry.install(s, skill_id, skill.manifest.permissions)


def test_the_owner_routes(owner, browser, monkeypatch):
    body = {"skill_id": "acme/notes", "version": "1.0.0", "effects": ["send"]}
    shown = owner.post("/app/skill-releases", json=body).json()
    [rel] = shown["releases"]
    assert rel["effect_class"] == "send" and rel["available"] is False
    assert owner.post("/app/skill-releases", json={**body, "effects": ["nope"]}).status_code == 422
    assert (
        owner.post("/app/skill-releases", json={**body, "version": "1.0.1", "patch_signature": "0" * 64}).status_code
        == 422
    )
    retired = owner.post("/app/skill-releases/retire", json={"skill_id": "acme/gone", "reason": "withdrawn"}).json()
    assert [t["skill_id"] for t in retired["retired"]] == ["acme/gone"]
    assert owner.post("/app/skill-releases", json={**body, "skill_id": "acme/gone"}).status_code == 410
    assert owner.get("/app/skill-releases").json()["retired"][0]["reason"] == "withdrawn"
    monkeypatch.setattr(get_settings(), "skill_cooldown_enabled", False)
    assert owner.get("/app/skill-releases").status_code == 404
    assert browser().get("/app/skill-releases").status_code == 401


def test_a_stranger_sees_nothing(browser):
    stranger = browser()
    signup(stranger, name="Mallory Tan", email="mallory@example.com")
    assert stranger.get("/app/skill-releases").status_code == 403
