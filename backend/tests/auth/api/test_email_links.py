"""ADR 0009 T9: verifying an email address and resetting a forgotten password by emailed link,
plus the browser's time zone at sign-up (T10)."""

from __future__ import annotations

import re
import smtplib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from superaide import mail
from superaide.auth.models import EmailToken
from superaide.platform.config import get_settings
from superaide.platform.db.models import OwnerPreference, UserAccount
from superaide.platform.db.session import session_scope
from tests.api_support import OWNER, PASSWORD, hire, login, signup

NEW_PASSWORD = "a brand new long passphrase"


@pytest.fixture(autouse=True)
def outbox() -> list[mail.Mail]:
    sent = mail._dev_mailer.sent
    sent.clear()
    return sent


def token_in(m: mail.Mail, path: str) -> str:
    found = re.search(rf"{re.escape(path)}#token=([A-Za-z0-9_-]+)", m.text)
    assert found, m.text
    return found.group(1)


def account() -> UserAccount:
    with session_scope() as s:
        user = s.scalar(select(UserAccount).where(UserAccount.email_normalized == OWNER["email"]))
        assert user is not None
        s.expunge(user)
        return user


# --- verification -----------------------------------------------------------------------


def test_signing_up_emails_a_link_that_verifies_the_address(browser, outbox):
    c = browser()
    r = signup(c)
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is False
    [m] = outbox
    assert m.to == OWNER["email"] and "Confirm your email" in m.subject
    assert f"{get_settings().frontend_origin}/verify-email#token=" in m.text  # in the fragment, never sent to servers
    token = token_in(m, "/verify-email")
    assert browser().post("/auth/verify-email", json={"token": token}).status_code == 200  # no session needed
    assert account().email_verified_at is not None
    assert c.get("/auth/me").json()["user"]["email_verified"] is True
    # Each link works once.
    again = browser().post("/auth/verify-email", json={"token": token})
    assert again.status_code == 400 and "expired" in again.json()["detail"]


def test_a_new_link_replaces_the_old_one_and_is_limited_per_hour(browser, outbox):
    c = browser()
    signup(c)
    first = token_in(outbox[0], "/verify-email")
    assert c.post("/auth/verify-email/send").json() == {"status": "sent"}
    assert browser().post("/auth/verify-email", json={"token": first}).status_code == 400
    for _ in range(get_settings().email_links_per_account_per_hour - 2):
        assert c.post("/auth/verify-email/send").status_code == 200
    assert c.post("/auth/verify-email/send").status_code == 429


def test_resending_needs_a_session_and_says_when_already_verified(browser, outbox):
    assert browser().post("/auth/verify-email/send").status_code == 401
    c = browser()
    signup(c)
    browser().post("/auth/verify-email", json={"token": token_in(outbox[0], "/verify-email")})
    assert c.post("/auth/verify-email/send").json() == {"status": "already verified"}


@pytest.mark.parametrize("token", ["x" * 16, "not-a-real-token-at-all-1234567890", "A" * 200])
def test_made_up_tokens_are_refused(browser, token):
    r = browser().post("/auth/verify-email", json={"token": token})
    assert r.status_code == 400


def test_an_expired_link_is_refused(browser, outbox):
    signup(browser())
    with session_scope() as s:
        s.execute(update(EmailToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    r = browser().post("/auth/verify-email", json={"token": token_in(outbox[0], "/verify-email")})
    assert r.status_code == 400 and account().email_verified_at is None


def test_hiring_can_require_a_verified_email(browser, outbox, monkeypatch):
    monkeypatch.setattr(get_settings(), "require_verified_email", True)
    c = browser()
    signup(c)
    r = hire(c)
    assert r.status_code == 403 and "confirm your email" in r.json()["detail"]
    browser().post("/auth/verify-email", json={"token": token_in(outbox[0], "/verify-email")})
    assert hire(c).status_code == 200


def test_hiring_does_not_require_verification_by_default(browser):
    c = browser()
    signup(c)
    assert hire(c).status_code == 200


# --- password reset -----------------------------------------------------------------------


def test_forgot_password_answers_the_same_whether_or_not_the_account_exists(browser, outbox):
    signup(browser())
    outbox.clear()
    known = browser().post("/auth/password-reset/request", json={"email": OWNER["email"]})
    unknown = browser().post("/auth/password-reset/request", json={"email": "nobody@example.com"})
    junk = browser().post("/auth/password-reset/request", json={"email": "not an email"})
    assert known.status_code == unknown.status_code == junk.status_code == 200
    assert known.json() == unknown.json() == junk.json()
    [m] = outbox
    assert m.to == OWNER["email"] and "Reset your Superaide password" in m.subject


def test_resetting_sets_the_password_signs_out_everywhere_and_verifies_the_address(browser, outbox):
    c = browser()
    signup(c)
    browser().post("/auth/password-reset/request", json={"email": OWNER["email"]})
    token = token_in(outbox[-1], "/reset-password")
    weak = browser().post("/auth/password-reset", json={"token": token, "password": "short"})
    assert weak.status_code == 422  # and the link still works for a better password:
    assert browser().post("/auth/password-reset", json={"token": token, "password": NEW_PASSWORD}).status_code == 200
    assert c.get("/auth/me").status_code == 401  # the old session was signed out
    assert login(browser(), password=PASSWORD).status_code == 401
    assert login(browser(), password=NEW_PASSWORD).status_code == 200
    assert account().email_verified_at is not None
    again = browser().post("/auth/password-reset", json={"token": token, "password": NEW_PASSWORD + "!"})
    assert again.status_code == 400


def test_a_verify_link_cannot_reset_a_password(browser, outbox):
    signup(browser())
    token = token_in(outbox[0], "/verify-email")
    r = browser().post("/auth/password-reset", json={"token": token, "password": NEW_PASSWORD})
    assert r.status_code == 400


def test_a_link_stops_working_if_the_address_changed(browser, outbox):
    signup(browser())
    with session_scope() as s:
        s.execute(update(UserAccount).values(email="changed@example.com", email_normalized="changed@example.com"))
    r = browser().post("/auth/verify-email", json={"token": token_in(outbox[0], "/verify-email")})
    assert r.status_code == 400


def test_a_mail_server_failure_never_breaks_sign_up(browser, monkeypatch):
    class Broken:
        def send(self, m: mail.Mail) -> None:
            raise mail.MailError("down")

    monkeypatch.setattr("superaide.auth.routes.get_mailer", lambda: Broken())
    assert signup(browser()).status_code == 200


# --- the SMTP adapter -------------------------------------------------------------------------


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: float = 0, **_: Any) -> None:
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_: Any) -> None:
        self.calls.append(("quit",))

    def starttls(self, context: Any = None) -> None:
        self.calls.append(("starttls",))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def send_message(self, msg: Any) -> None:
        self.calls.append(("send", msg["To"], msg["Subject"], msg["From"]))


def test_the_smtp_mailer_uses_starttls_and_logs_in(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings = get_settings().model_copy(
        update={"mail_backend": "smtp", "smtp_host": "smtp.example.com", "smtp_username": "u", "smtp_password": "p"}
    )
    mailer = mail.get_mailer(settings)
    mailer.send(mail.Mail(to="a@example.com", subject="Hi", text="Hello"))
    [server] = FakeSMTP.instances
    assert (server.host, server.port) == ("smtp.example.com", 587)
    assert server.calls[:2] == [("starttls",), ("login", "u")]
    assert server.calls[2][:3] == ("send", "a@example.com", "Hi")


def test_the_smtp_mailer_reports_failures_as_mail_errors(monkeypatch):
    def refuse(*_: Any, **__: Any) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    mailer = mail.SmtpMailer(host="localhost", port=2525, sender="x@example.com")
    with pytest.raises(mail.MailError):
        mailer.send(mail.Mail(to="a@example.com", subject="Hi", text="Hello"))


def test_smtp_needs_a_host_and_a_known_security_mode():
    with pytest.raises(mail.MailError):
        mail.SmtpMailer(host="", port=25, sender="x@example.com")
    with pytest.raises(mail.MailError):
        mail.SmtpMailer(host="h", port=25, sender="x@example.com", security="tls1.0")


# --- the time zone at sign-up (T10) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "stored"),
    [("Asia/Kolkata", "Asia/Kolkata"), ("Europe/Berlin", "Europe/Berlin"), (None, "UTC"), ("Mars/Olympus", "UTC")],
)
def test_sign_up_keeps_the_browsers_time_zone_or_falls_back_to_utc(browser, sent, stored):
    c = browser()
    r = signup(c, timezone=sent)
    assert r.status_code == 200 and r.json()["user"]["timezone"] == stored
    assert hire(c).status_code == 200
    assert c.get("/app/preferences").json()["timezone"] == stored  # the owner's settings start in that zone
    with session_scope() as s:
        pref = s.scalar(select(OwnerPreference))
        assert pref is not None and pref.timezone == stored


# --- the address links point at ------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, "http://localhost:3000"),  # nothing to go on: the server's own address
        ({"Origin": "http://127.0.0.1:3000"}, "http://127.0.0.1:3000"),
        ({"Origin": "https://ava-demo.trycloudflare.com"}, "https://ava-demo.trycloudflare.com"),  # LINK_ORIGINS
        ({"Origin": "http://192.168.1.20:3000"}, "http://192.168.1.20:3000"),  # DEV_ORIGINS (a phone on Wi-Fi)
        (
            {"X-Forwarded-Host": "ava-demo.trycloudflare.com", "X-Forwarded-Proto": "https"},
            "https://ava-demo.trycloudflare.com",
        ),
        # Never someone else's site, however the request dresses it up.
        ({"Origin": "https://evil.example"}, "http://localhost:3000"),
        ({"Origin": "https://ava-demo.trycloudflare.com.evil.example"}, "http://localhost:3000"),
        ({"Origin": "https://user@ava-demo.trycloudflare.com"}, "http://localhost:3000"),
        ({"Origin": "javascript://ava-demo.trycloudflare.com"}, "http://localhost:3000"),
        ({"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"}, "http://localhost:3000"),
        ({"Origin": "http://ava-demo.trycloudflare.com"}, "http://localhost:3000"),  # the pattern is https only
    ],
)
def test_links_use_the_address_the_person_is_on_when_it_is_ours(browser, outbox, monkeypatch, headers, expected):
    settings = get_settings()
    monkeypatch.setattr(settings, "link_origins", "https://*.trycloudflare.com")
    monkeypatch.setattr(settings, "dev_origins", "192.168.1.20")
    browser().post("/auth/signup", json={**OWNER, "timezone": "UTC"}, headers=headers)
    [m] = outbox
    assert f"{expected}/verify-email#token=" in m.text, m.text


def test_reset_links_use_the_public_address_when_the_request_gives_none(browser, outbox, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", "https://ava.example.com")
    signup(browser())
    outbox.clear()
    browser().post(
        "/auth/password-reset/request", json={"email": OWNER["email"]}, headers={"Origin": "https://evil.example"}
    )
    [m] = outbox
    assert "https://ava.example.com/reset-password#token=" in m.text
