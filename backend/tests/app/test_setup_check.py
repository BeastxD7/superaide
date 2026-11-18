"""'Check my setup' (#567): the in-app subset of the doctor, owner only, and the console Gaps row."""

from __future__ import annotations

from superaide import doctor
from superaide.platform.config import Settings, get_settings
from tests.api_support import *  # noqa: F403  (the HTTP API fixtures: app, browser, owner, other)
from tests.api_support import CSRF

ADMIN = {"Authorization": "Bearer test-admin-token"}
# Deliberately NOT in the in-app list: they are about the machine, not the owner's account.
SERVER_ONLY = ("Settings file", "Migrations", "Ports", "Telegram", "Slack")


def leaky(**kw) -> Settings:
    base = {"public_base_url": "", "frontend_origin": "http://localhost:3000", "session_cookie_secure": True}
    return Settings(_env_file=None, **{**base, **kw})


def test_the_in_app_subset_only_runs_the_chosen_checks():
    results = doctor.run_in_app(leaky(), http=None)
    names = " ".join(r.name for r in results)
    assert "Spend cap" in names and "Public address" in names
    for server_only in ("Settings file", "Migrations", "Ports", "Telegram", "Slack"):
        assert server_only not in names, server_only
    assert set(doctor.IN_APP_CHECKS).isdisjoint(SERVER_ONLY)


def test_the_owner_runs_the_check_and_sees_the_localhost_problem(owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "session_cookie_secure", True)
    monkeypatch.setattr(get_settings(), "public_base_url", "")
    r = owner.post("/app/setup-check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["fails"] >= 1
    row = next(x for x in body["results"] if x["name"].startswith("Public address"))
    assert row["status"] == "fail" and "PUBLIC_BASE_URL" in row["fix"]
    assert "sk-ant" not in r.text and "test-admin-token" not in r.text  # names settings, never values


def test_a_clean_setup_reports_ok(owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "session_cookie_secure", False)
    monkeypatch.setattr(get_settings(), "public_base_url", "")
    body = owner.post("/app/setup-check").json()
    row = next(x for x in body["results"] if x["name"].startswith("Public address"))
    assert row["status"] == "ok"


def test_the_check_needs_a_signed_in_owner(owner, other, browser):
    assert browser().post("/app/setup-check").status_code == 401
    assert other.post("/app/setup-check").status_code == 403  # signed in, but has not hired Ava
    assert owner.post("/app/setup-check", headers={"X-Requested-With": ""}).status_code in (400, 403)
    assert owner.post("/app/setup-check", headers={"Authorization": "Bearer test-admin-token"}).status_code == 200


def test_the_console_gaps_list_has_a_localhost_row_only_when_it_applies(app, browser, monkeypatch):
    c = browser()
    s = get_settings()
    monkeypatch.setattr(s, "public_base_url", "")
    monkeypatch.setattr(s, "session_cookie_secure", True)
    gaps = c.get("/api/overview", headers=ADMIN).json()["gaps"]
    assert [g["label"] for g in gaps] == ["Links in emails point at localhost"]
    assert "PUBLIC_BASE_URL" in gaps[0]["fix"]
    monkeypatch.setattr(s, "session_cookie_secure", False)
    assert c.get("/api/overview", headers=ADMIN).json()["gaps"] == []
    monkeypatch.setattr(s, "public_base_url", "https://ava.example.com")
    monkeypatch.setattr(s, "session_cookie_secure", True)
    assert c.get("/api/overview", headers=ADMIN).json()["gaps"] == []


_ = CSRF
