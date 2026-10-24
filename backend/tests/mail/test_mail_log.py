"""The log mailer prints the whole email by default; MAIL_LOG_BODY=false keeps links out of the log."""

from __future__ import annotations

import logging

from superaide.config import get_settings
from superaide.mail import LogMailer, Mail

LINK = "http://localhost:3000/verify-email#token=SECRET-TOKEN"


def _mail() -> Mail:
    return Mail(to="a@example.com", subject="Confirm your email", text=f"Open this link: {LINK}")


def test_the_log_mailer_prints_the_link_by_default(caplog):
    with caplog.at_level(logging.WARNING, logger="superaide.mail"):
        mailer = LogMailer()
        mailer.send(_mail())
    assert LINK in caplog.text
    assert len(mailer.sent) == 1


def test_mail_log_body_off_logs_only_recipient_and_subject(caplog, monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_log_body", False)
    with caplog.at_level(logging.WARNING, logger="superaide.mail"):
        mailer = LogMailer()
        mailer.send(_mail())
    assert "a@example.com" in caplog.text and "Confirm your email" in caplog.text
    assert "SECRET-TOKEN" not in caplog.text
    assert len(mailer.sent) == 1  # still kept for the tests that read it
