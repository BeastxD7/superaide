"""Accounts: sign-up, login, sessions, lockout, CSRF and password changes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from superaide.identity.auth import service
from superaide.identity.auth.models import AuthEvent, UserAccount, UserSession
from superaide.identity.models import Person
from superaide.main import create_app
from superaide.platform.db.session import session_scope

PASSWORD = "correct horse battery"
EMAIL = "Jordan@Example.com"


@pytest.fixture
def browser(agent):
    app = create_app(agent)
    return lambda: TestClient(app, headers={"X-Requested-With": "superaide"})


@pytest.fixture
def client(browser):
    return browser()


def signup(client, email=EMAIL, password=PASSWORD, name="Jordan Whitfield"):
    return client.post("/auth/signup", json={"name": name, "email": email, "password": password})


def login(client, email=EMAIL, password=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_signup_sets_a_safe_cookie_and_stores_only_a_hash(client):
    r = signup(client)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == EMAIL and r.json()["hire"] is None
    cookie = r.headers["set-cookie"].lower()
    assert "superaide_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    with session_scope() as s:
        user = s.scalar(select(UserAccount))
        assert user.email_normalized == "jordan@example.com"
        assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash
        # An account belongs to no Ava until it hires, so there is no Person in the agents' world yet.
        assert user.person_id is None and s.scalar(select(Person)) is None
        token = client.cookies.get("superaide_session")
        stored = s.scalar(select(UserSession.token_hash))
        assert token not in stored and stored == service.token_hash(token)
    assert client.get("/auth/me").json()["user"]["name"] == "Jordan Whitfield"


def test_signup_checks_email_password_and_duplicates(client, browser):
    bad_email = signup(client, email="not-an-email")
    assert bad_email.status_code == 422 and bad_email.json()["detail"]["field"] == "email"
    weak = signup(client, password="password1")
    assert weak.status_code == 422 and weak.json()["detail"]["field"] == "password"
    named = signup(client, password="jordanwhitfield2026!")
    assert named.status_code == 422  # contains the name
    assert signup(client).status_code == 200
    again = signup(browser(), email="JORDAN@example.com")  # same address, different case
    assert again.status_code == 409


def test_login_logout_and_generic_errors(client, browser):
    signup(client)
    other = browser()
    wrong = login(other, password="not the password at all")
    unknown = login(other, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]  # doesn't reveal which emails exist
    assert login(other).status_code == 200
    assert other.get("/auth/me").status_code == 200
    old_cookie = other.cookies.get("superaide_session")
    assert other.post("/auth/logout").status_code == 200
    # The server revoked it: replaying the old cookie doesn't work.
    replay = browser()
    replay.cookies.set("superaide_session", old_cookie)
    assert replay.get("/auth/me").status_code == 401


def test_account_locks_after_repeated_failures_and_the_attempts_are_recorded(client, browser, settings):
    signup(client)
    attacker = browser()
    for _ in range(settings.login_max_failures):
        assert login(attacker, password="wrong password guess").status_code == 401
    # Now even the right password is refused until the lock expires.
    locked = login(attacker)
    assert locked.status_code == 429 and "Try again in" in locked.json()["detail"]
    with session_scope() as s:
        fails = s.scalar(select(func.count()).select_from(AuthEvent).where(AuthEvent.kind == "login_fail"))
        assert fails == settings.login_max_failures  # failures survive the error responses
        s.execute(update(UserAccount).values(locked_until=datetime.now(UTC) - timedelta(seconds=1)))
    assert login(attacker).status_code == 200


def test_too_many_failures_from_one_device_are_throttled(client, browser, settings, monkeypatch):
    monkeypatch.setattr(settings, "login_failures_per_ip", 3)
    for i in range(3):
        login(client, email=f"guess{i}@example.com")
    assert login(client, email="another@example.com").status_code == 429


def test_expired_sessions_are_refused(client):
    signup(client)
    with session_scope() as s:
        s.execute(update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    assert client.get("/auth/me").status_code == 401


def test_changes_need_the_csrf_header(agent):
    plain = TestClient(create_app(agent))  # no X-Requested-With, like a form posted from another site
    assert signup(plain).status_code == 403
    assert plain.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 403


def test_changing_password_signs_out_other_devices(client, browser):
    signup(client)
    laptop = browser()
    login(laptop)
    r = client.post("/auth/password", json={"current": PASSWORD, "new": "a brand new passphrase"})
    assert r.status_code == 200, r.text
    assert client.get("/auth/me").status_code == 200  # this device stays signed in
    assert laptop.get("/auth/me").status_code == 401  # the other one is out
    assert login(browser(), password="a brand new passphrase").status_code == 200
    bad = client.post("/auth/password", json={"current": "wrong", "new": "another new passphrase"})
    assert bad.status_code == 401


def test_operator_can_reset_a_password_from_the_terminal(client, browser):
    signup(client)
    with session_scope() as s:
        service.reset_password(s, EMAIL, "terminal reset passphrase")
    assert client.get("/auth/me").status_code == 401  # signed out everywhere
    assert login(browser(), password="terminal reset passphrase").status_code == 200


def test_an_email_without_an_account_locks_the_same_way(client, browser, settings):
    # Otherwise "Too many wrong passwords" would tell a stranger which emails are registered.
    signup(client)
    real, made_up = browser(), browser()
    answers: dict[str, list[tuple[int, str]]] = {"real": [], "made_up": []}
    for _ in range(settings.login_max_failures + 1):
        for who, b, email in (("real", real, EMAIL), ("made_up", made_up, "nobody@example.com")):
            r = login(b, email=email, password="wrong password guess")
            answers[who].append((r.status_code, r.json()["detail"].split(" Try again")[0]))
    assert answers["real"] == answers["made_up"]
    assert answers["made_up"][-1][0] == 429
