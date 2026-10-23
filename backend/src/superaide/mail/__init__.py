"""Sending email: one small interface, a development mailer and an SMTP adapter.

No provider is chosen here. Any provider that speaks SMTP (most do) works through
`SmtpMailer`; a provider's own HTTP API can be added later as another `Mailer`.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

from superaide.config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str


class MailError(RuntimeError):
    """The message could not be handed to the mail server."""


class Mailer(Protocol):
    def send(self, mail: Mail) -> None: ...


class LogMailer:
    """Development: writes each email to the server log (links included) and keeps it in `sent`."""

    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> None:
        self.sent.append(mail)
        # WARNING, not INFO: the dev server only shows warnings, and this is the only way to get the link.
        if not get_settings().mail_log_body:
            log.warning("email (not sent, MAIL_BACKEND=log) to %s: %s", mail.to, mail.subject)
            return
        log.warning("email (not sent, MAIL_BACKEND=log) to %s: %s\n%s", mail.to, mail.subject, mail.text)


class SmtpMailer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str = "",
        password: str = "",
        security: str = "starttls",
        timeout: float = 15.0,
    ) -> None:
        if not host:
            raise MailError("SMTP_HOST is not set")
        if security not in {"starttls", "ssl", "none"}:
            raise MailError("SMTP_SECURITY must be starttls, ssl or none")
        self.host, self.port, self.sender = host, port, sender
        self.username, self.password, self.security, self.timeout = username, password, security, timeout

    def message(self, mail: Mail) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=self.sender.rsplit("@", 1)[-1].strip("> ") or None)
        msg.set_content(mail.text)
        return msg

    def send(self, mail: Mail) -> None:
        context = ssl.create_default_context()
        try:
            if self.security == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with server:
                if self.security == "starttls":
                    server.starttls(context=context)
                if self.username:
                    server.login(self.username, self.password)
                server.send_message(self.message(mail))
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"couldn't send email: {type(exc).__name__}") from exc


_dev_mailer = LogMailer()


def get_mailer(settings: Settings | None = None) -> Mailer:
    settings = settings or get_settings()
    if settings.mail_backend == "smtp":
        return SmtpMailer(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=settings.mail_from,
            username=settings.smtp_username,
            password=settings.smtp_password,
            security=settings.smtp_security,
            timeout=settings.smtp_timeout_seconds,
        )
    return _dev_mailer
