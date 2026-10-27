"""Issue #13: the introduction starts by setting up Ava's brain (credits, own key or skip), and the
helpers the voice introduction shares. Driven through the real app, in hosted mode."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app.intro_brain import brain_ready_for_intro, intro_needs_brain_step
from superaide.identity.vault import store
from superaide.identity.vault.store import runtime_settings
from superaide.main import create_app
from superaide.platform.config import get_settings
from superaide.platform.db.models import Document, Event, Job
from superaide.platform.db.session import session_scope

PLATFORM_KEY = "sk-ant-platform-secret-key-000000000000"
OWN_KEY = "sk-ant-owner-own-key-0000000000000000"


@pytest.fixture
def env(monkeypatch):
    e = get_settings()
    monkeypatch.setattr(e, "deploy_mode", "hosted")
    monkeypatch.setattr(e, "fake_llm", False)
    monkeypatch.setattr(e, "platform_ai_enabled", True)
    monkeypatch.setattr(e, "platform_anthropic_api_key", PLATFORM_KEY)
    monkeypatch.setattr(e, "platform_daily_cap_usd", 5.0)
    monkeypatch.setattr(e, "run_channels_in_api", False)
    monkeypatch.setattr(e, "run_jobs_in_api", False)
    return e


def new_owner(email: str = "intro@example.com") -> TestClient:
    c = TestClient(create_app(), headers={"X-Requested-With": "superaide"})
    assert (
        c.post(
            "/auth/signup", json={"name": "Intro Owner", "email": email, "password": "correct horse battery"}
        ).status_code
        == 200
    )
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c


def test_with_no_brain_the_introduction_starts_by_choosing_one(env):
    c = new_owner()
    view = c.get("/app/onboarding").json()
    assert view["step"] == "brain" and view["index"] == 0 and view["total"] == 9 and view["labels"][0] == "Brain"
    brain = view["input"]
    assert brain["kind"] == "brain" and brain["ready"] is False and brain["credits"]["available"] is True
    assert brain["credits"]["opted_in"] is False and PLATFORM_KEY not in str(view)
    first_lines = " ".join(view["ava"])
    assert "AI brain" in first_lines and "skip" in first_lines.lower()


def test_credits_in_one_click_then_the_introduction_carries_on(env):
    c = new_owner()
    c.get("/app/onboarding")
    after = c.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "credits"}}).json()
    assert after["step"] == "hello" and after["index"] == 1 and after["total"] == 9
    assert runtime_settings().use_platform_ai is True
    assert c.get("/app/me").json()["brain"] == "claude"
    with session_scope() as s:
        said = " ".join(e.content for e in s.scalars(select(Event).where(Event.direction == "out")))
        assert "Superaide AI credits" in said and PLATFORM_KEY not in said


def test_credits_are_refused_when_the_server_does_not_offer_them(env, monkeypatch):
    monkeypatch.setattr(env, "platform_ai_enabled", False)
    c = new_owner()
    view = c.get("/app/onboarding").json()
    assert view["input"]["credits"] is None
    again = c.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "credits"}}).json()
    assert again["step"] == "brain" and "aren't offered" in again["hint"]
    assert runtime_settings().use_platform_ai is False


def test_own_key_needs_a_key_first_and_then_works(env):
    c = new_owner()
    c.get("/app/onboarding")
    stuck = c.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "own"}}).json()
    assert stuck["step"] == "brain" and "can't see your key" in stuck["hint"]
    with session_scope() as s:
        store.save(s, "anthropic", {"api_key": OWN_KEY}, user_id=None, meta={})
    assert brain_ready_for_intro(runtime_settings())
    ok = c.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "own"}}).json()
    assert ok["step"] == "hello" and runtime_settings().use_platform_ai is False  # their key, not credits


def test_skipping_keeps_what_is_shared_until_a_brain_is_set_up(env):
    c = new_owner()
    c.get("/app/onboarding")
    assert c.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "skip"}}).json()["step"] == "hello"
    assert runtime_settings().use_platform_ai is False
    # (The step after a skip is the normal introduction; a bad choice never advances.)
    c2 = new_owner("second@example.com")
    c2.get("/app/onboarding")
    bad = c2.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "whatever"}}).json()
    assert bad["step"] == "brain" and "Pick one" in bad["hint"]


def test_enabling_credits_later_queues_what_was_shared_while_skipping(env):
    c = new_owner()
    c.get("/app/onboarding")
    c.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "skip"}})
    link = c.post("/app/intro/links", json={"url": "https://93.184.216.34/about"})
    assert link.status_code in (200, 400, 422, 502)  # no network here: a failed fetch is fine, the point is below
    with session_scope() as s:
        s.add(Document(owner_person_id=_owner_id(s), kind="text", title="resume", source="resume.txt", sha256="x" * 64,
                       size_bytes=5, text="I lead AI at Acme.", status="stored", warnings=[]))  # fmt: skip
    assert c.post("/app/providers/credits", json={"on": True}).status_code == 200
    with session_scope() as s:
        assert s.scalar(select(Job).where(Job.kind == "read_pending")) is not None  # read as soon as there is a brain


def _owner_id(s):
    from superaide.app.accounts import owner

    person = owner(s)
    assert person is not None
    return person.id


def test_the_voice_introduction_shares_the_readiness_check(env, monkeypatch):
    settings = runtime_settings()
    assert brain_ready_for_intro(settings) is False and intro_needs_brain_step(settings) is True
    forced_offline = settings.model_copy(update={"fake_llm": True})
    assert intro_needs_brain_step(forced_offline) is False  # the operator chose offline mode: nothing to set up
    ready = settings.model_copy(update={"anthropic_api_key": OWN_KEY})
    assert brain_ready_for_intro(ready) is True and intro_needs_brain_step(ready) is False


def test_an_owner_who_already_has_a_brain_never_sees_the_step(env, monkeypatch):
    monkeypatch.setattr(env, "deploy_mode", "self_hosted")
    monkeypatch.setattr(env, "anthropic_api_key", OWN_KEY)
    view = new_owner("has-key@example.com").get("/app/onboarding").json()
    assert view["step"] == "hello" and view["total"] == 8 and view["labels"][0] == "Hello"


def test_own_key_is_refused_on_the_strength_of_the_operators_server_key(env, monkeypatch):
    """An operator's own Ava has a ready brain from the server key, but "my own key" needs one it saved."""
    monkeypatch.setattr(env, "platform_key_owner_emails", "operator@example.com")
    monkeypatch.setattr(env, "anthropic_api_key", PLATFORM_KEY)
    c = new_owner("operator@example.com")
    with session_scope() as s:
        assert brain_ready_for_intro(runtime_settings())  # ready, on the server's key
    stuck = c.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "own"}})
    view = stuck.json()
    assert view["step"] == "brain" and "AI model page" in view["hint"]
    with session_scope() as s:
        store.save(s, "anthropic", {"api_key": OWN_KEY}, user_id=None, meta={})
    ok = c.post("/app/onboarding", json={"step": "brain", "answer": {"choice": "own"}}).json()
    assert ok["step"] == "hello"
