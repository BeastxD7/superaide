"""Telegram and Slack listeners running inside the API process.

They follow the current connections: connecting Telegram in the app starts its
listener, changing the token restarts it, disconnecting stops it. No restart
of the app and no separate processes are needed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from superaide.channels.base import InboundMessage
from superaide.config import Settings

log = logging.getLogger(__name__)


class ChannelRunner:
    def __init__(self, handle: Callable[[InboundMessage], Any]) -> None:
        self.handle = handle
        self._lock = threading.Lock()
        self._telegram: tuple[str, threading.Event, threading.Thread] | None = None
        self._slack: tuple[tuple[str, str], Any] | None = None

    def ensure(self, settings: Settings) -> dict[str, bool]:
        """Start, restart or stop listeners to match these settings."""
        with self._lock:
            self._ensure_telegram(settings.telegram_bot_token)
            self._ensure_slack(settings.slack_bot_token, settings.slack_app_token, settings.slack_team_id)
            return {"telegram": self._telegram is not None, "slack": self._slack is not None}

    def stop(self) -> None:
        with self._lock:
            self._ensure_telegram("")
            self._ensure_slack("", "", "")

    def _ensure_telegram(self, token: str) -> None:
        if self._telegram and self._telegram[0] != token:
            self._telegram[1].set()  # the thread exits after its current poll
            self._telegram = None
            log.info("Telegram listener stopped")
        if token and self._telegram is None:
            from superaide.channels.telegram import poll_forever

            stop = threading.Event()
            thread = threading.Thread(
                target=poll_forever, args=(token, self.handle, stop), name="telegram-listener", daemon=True
            )
            thread.start()
            self._telegram = (token, stop, thread)

    def _ensure_slack(self, bot: str, app: str, team: str) -> None:
        key = (bot, app)
        if self._slack and self._slack[0] != key:
            try:
                self._slack[1].close()
            except Exception:
                log.exception("closing Slack listener")
            self._slack = None
            log.info("Slack listener stopped")
        if bot and app and self._slack is None:
            from superaide.channels.slack import socket_mode_handler

            try:
                handler = socket_mode_handler(bot, app, team, self.handle)
                handler.connect()
                self._slack = (key, handler)
            except Exception:
                log.exception("Slack listener failed to start")
