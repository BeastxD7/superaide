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
        # Many owners (ADR 0009 T4): one poller per owner bot, each running inside its owner's instance.
        self._bots: dict[str, tuple[Any, threading.Event, threading.Thread]] = {}
        self._slack: tuple[tuple[str, str], Any] | None = None

    def ensure(self, settings: Settings) -> dict[str, bool]:
        """Start, restart or stop listeners to match these settings."""
        with self._lock:
            self._ensure_telegram(settings.telegram_bot_token)
            self._ensure_slack(settings.slack_bot_token, settings.slack_app_token, settings.slack_team_id)
            return {"telegram": self._telegram is not None, "slack": self._slack is not None}

    def ensure_bots(self, bots: dict[str, Any]) -> list[str]:
        """Run exactly one poller per bot token in `bots` (token -> instance scope), each inside that
        instance, so what it receives is handled as that owner's. Returns the bot ids running."""
        from superaide.channels.telegram import bot_id, poll_forever
        from superaide.tenancy.context import use_instance

        with self._lock:
            for token, (scope, stop, thread) in list(self._bots.items()):
                if token not in bots or bots[token] != scope or not thread.is_alive():
                    stop.set()  # the thread exits after its current poll
                    del self._bots[token]
            for token, scope in bots.items():
                if token in self._bots:
                    continue
                stop = threading.Event()

                def run(token: str = token, scope: Any = scope, stop: threading.Event = stop) -> None:
                    with use_instance(scope):
                        poll_forever(token, self.handle, stop)

                thread = threading.Thread(target=run, name=f"telegram-{bot_id(token)}", daemon=True)
                thread.start()
                self._bots[token] = (scope, stop, thread)
            return sorted(bot_id(t) for t in self._bots)

    def stop(self) -> None:
        with self._lock:
            self._ensure_telegram("")
            self._ensure_slack("", "", "")
            for _, stop, _ in self._bots.values():
                stop.set()
            self._bots.clear()

    def _ensure_telegram(self, token: str) -> None:
        if self._telegram and self._telegram[0] == token and not self._telegram[2].is_alive():
            log.warning("Telegram listener had stopped; starting it again")
            self._telegram = None
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
            from superaide.channels import health
            from superaide.channels.slack import auth_error, socket_mode_handler

            try:
                handler = socket_mode_handler(bot, app, team, self.handle)
                handler.connect()
                self._slack = (key, handler)
            except Exception as exc:
                log.exception("Slack listener failed to start")
                code = auth_error(exc)
                if code:
                    health.record("slack", health.AUTH_FAILED, f"Slack rejected the token ({code})")
            else:
                health.record("slack", health.OK)
