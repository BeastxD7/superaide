"""A Telegram bot token must never reach a log line, however the transport error was worded (issue #93, SEC-11)."""

from __future__ import annotations

import io
import logging
import threading

import httpx
import pytest

from superaide.channels import telegram
from superaide.security import logredact

TOKEN = "123456789:AAH-secret_tokenvalue0123456789abcdefg"


@pytest.fixture
def redacting():
    logredact.install()
    yield


def test_a_token_in_a_log_message_or_argument_is_masked(redacting, caplog):
    log = logging.getLogger("superaide.test")
    with caplog.at_level(logging.INFO, logger="superaide.test"):
        log.warning("failed at https://api.telegram.org/bot%s/getUpdates", TOKEN)
        log.warning("literal bot%s in the text" % TOKEN)  # noqa: UP031
    assert TOKEN not in caplog.text and "AAH-secret" not in caplog.text
    assert "bot[redacted]" in caplog.text


def test_a_token_in_a_traceback_is_masked(redacting, caplog):
    log = logging.getLogger("superaide.test")
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    stream = io.StringIO()
    handler.stream = stream
    log.addHandler(handler)
    try:
        try:
            raise httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{TOKEN}/getUpdates")
        except httpx.ConnectError:
            log.exception("boom")
    finally:
        log.removeHandler(handler)
    assert TOKEN not in stream.getvalue() and "boom" in stream.getvalue()


def test_the_poller_logs_only_the_error_type_when_deleting_the_webhook_fails(caplog, monkeypatch):
    class Boom:
        def post(self, url, **kw):
            raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(telegram.httpx, "Client", lambda **kw: Boom())
    stop = threading.Event()
    stop.set()  # run the set-up call, skip the polling loop
    with caplog.at_level(logging.WARNING, logger=telegram.log.name):
        telegram.poll_forever(TOKEN, lambda m: None, stop)
    assert "deleteWebhook failed" in caplog.text and TOKEN not in caplog.text
