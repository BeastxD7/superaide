"""Keep secrets out of log lines (issue #93, SEC-11).

A Telegram bot token travels in the URL (`/bot<id>:<secret>/method`), so a transport error that prints
its request URL puts the token in the server log, which operators paste into issues. Every record is
rewritten as it is made, so no handler, logger or library has to remember: the message, its arguments
and any traceback lose the token. Other log hygiene (no message text, no addresses) stays at the call sites.
"""

from __future__ import annotations

import logging
import re
import traceback
from typing import Any

TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]{30,}")
MASK = "bot[redacted]"

_installed = False


def redact(text: str) -> str:
    return TOKEN.sub(MASK, text)


def install() -> None:
    """Wrap the log record factory once. Safe to call again."""
    global _installed
    if _installed:
        return
    make = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = make(*args, **kwargs)
        try:
            message = record.getMessage()
            if TOKEN.search(message):
                record.msg, record.args = redact(message), None
            if record.exc_info and record.exc_info[0] is not None:
                text = "".join(traceback.format_exception(*record.exc_info)).rstrip()
                if TOKEN.search(text):
                    record.exc_text = redact(text)  # the Formatter uses this instead of formatting it again
        except Exception:  # a logging helper must never break the caller
            pass
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
