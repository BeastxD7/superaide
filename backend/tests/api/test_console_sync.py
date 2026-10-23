"""The operator console says the same as the app: app-saved connections, forgotten notes, the identity gate."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from superaide.config import get_settings
from superaide.db.models import UserAccount
from superaide.db.session import session_scope
from superaide.tenancy.context import all_instances, use_instance
from superaide.tenancy.models import AgentInstance, InstanceMember
from superaide.vault import store
from tests.api.conftest import hire, signup

ADMIN = {"Authorization": "Bearer test-admin-token"}
TG_TOKEN = "123456789:AAEabcdefghijklmnopqrstuvwxyz0123456"


def overview(client) -> dict:
    r = client.get("/api/overview", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def scope_of_owner():
    from superaide.tenancy.context import InstanceScope

    with session_scope() as s:
        user = s.scalar(select(UserAccount))
        member = s.scalar(select(InstanceMember).where(InstanceMember.user_id == user.id))
        instance = s.get(AgentInstance, member.agent_id)
        return InstanceScope(tenant_id=instance.tenant_id, agent_id=instance.id)


@pytest.fixture
def owner_app(browser, monkeypatch):
    e = get_settings()
    monkeypatch.setattr(e, "deploy_mode", "hosted")  # the server's .env credentials are not an owner's
    monkeypatch.setattr(e, "fake_llm", False)
    monkeypatch.setattr(e, "telegram_bot_token", "")
    monkeypatch.setattr(e, "slack_bot_token", "")
    monkeypatch.setattr(e, "slack_app_token", "")
    monkeypatch.setattr(e, "anthropic_api_key", "")
    c = browser()
    signup(c), hire(c)
    return c


def test_nothing_connected_says_so(owner_app):
    live = overview(owner_app)
    assert live["channels"] == {"telegram": False, "slack": False}
    assert live["model_mode"].startswith("offline")


def test_connections_saved_in_the_app_show_as_connected_in_the_console(owner_app):
    with use_instance(scope_of_owner()), session_scope() as s:
        store.save(s, "telegram", {"bot_token": TG_TOKEN}, user_id=None, meta={})
        store.save(s, "slack", {"bot_token": "xoxb-fake-0000", "app_token": "xapp-fake-0000"}, user_id=None, meta={})
        store.save(s, "anthropic", {"api_key": "sk-ant-owner-own-key-0000000000000000"}, user_id=None, meta={})
    live = overview(owner_app)
    assert live["channels"] == {"telegram": True, "slack": True}
    assert live["model_mode"].startswith("Claude")


def test_a_forgotten_note_is_not_counted(owner_app):
    base = overview(owner_app)["counts"]["memories"]
    assert owner_app.post("/app/chat", json={"text": "Remember that my locker code is zq4417"}).status_code == 200
    one = overview(owner_app)["counts"]["memories"]
    assert one == base + 1
    [note] = [m for m in owner_app.get("/app/notebook").json()["memories"] if "zq4417" in m["text"]]
    assert owner_app.post(f"/app/notebook/{note['id']}/forget").status_code == 200
    assert overview(owner_app)["counts"]["memories"] == base


def test_signing_in_is_not_an_identity_failure_and_the_finished_introduction_is_counted(owner_app):
    live = overview(owner_app)
    gate = next(g for g in live["gates"] if g["label"] == "No identity failures")
    assert gate["status"] == "met" and gate["detail"].startswith("0 links")
    interview = next(g for g in live["gates"] if g["label"] == "Onboarding interview done")
    assert interview["status"] == "later" and "phase 2" not in interview["detail"]
    with all_instances(), session_scope() as s:
        from datetime import UTC, datetime

        from superaide.db.models import Onboarding

        for o in s.scalars(select(Onboarding)):
            o.completed_at = datetime.now(UTC)
    done = next(g for g in overview(owner_app)["gates"] if g["label"] == "Onboarding interview done")
    assert done["status"] == "met"
