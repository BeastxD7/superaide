"""Telegram and Slack listeners, in the API process (development) or the worker (a server).

They follow the current connections: connecting Telegram in the app starts its
listener, changing the token restarts it, disconnecting stops it.

One listener per bot across every process (ADR 0009 T11): before polling a bot (or
opening a Slack app's socket) a process takes a Postgres advisory lock for it, held
on its own connection for as long as it listens. Another worker, or an API left with
listeners on, finds the lock taken and leaves that bot alone; if the holder dies, its
connection closes, the lock frees, and the next refresh elsewhere picks the bot up.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from superaide.channels.base import InboundMessage
from superaide.platform.config import Settings

log = logging.getLogger(__name__)
LOCK_NAMESPACE = 784_221_002  # pg advisory lock namespace for "this process listens to that bot"


def claim_listener(key: str) -> Any:
    """A connection holding the listener lock for `key`, or None if another process holds it.
    Closing the connection releases the lock."""
    from sqlalchemy import text

    from superaide.platform.db.session import lock_engine

    conn = lock_engine().connect()
    try:
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(:ns, hashtext(:key))"), {"ns": LOCK_NAMESPACE, "key": key}
        ).scalar()
        conn.commit()
    except Exception:
        conn.close()
        raise
    if not got:
        conn.close()
        return None
    return conn


class ChannelRunner:
    def __init__(self, handle: Callable[[InboundMessage], Any]) -> None:
        self.handle = handle
        self._lock = threading.Lock()
        self._telegram: tuple[str, threading.Event, threading.Thread] | None = None
        # Many owners (ADR 0009 T4): one poller per owner bot, each running inside its owner's instance.
        self._bots: dict[str, tuple[Any, threading.Event, threading.Thread]] = {}
        # One Socket Mode connection per owner's own Slack app, its events handled in that owner's instance.
        self._apps: dict[str, tuple[Any, Any]] = {}  # app token -> (scope, _Held handler)
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
        from superaide.platform.tenancy.context import use_instance

        with self._lock:
            for token, (scope, stop, thread) in list(self._bots.items()):
                if token not in bots or bots[token] != scope or not thread.is_alive():
                    stop.set()  # the thread exits after its current poll
                    del self._bots[token]
            for token, scope in bots.items():
                if token in self._bots:
                    continue
                held = claim_listener(f"telegram:{bot_id(token)}")
                if held is None:
                    log.info("bot %s is polled by another process; skipped here", bot_id(token))
                    continue
                stop = threading.Event()

                def run(token: str = token, scope: Any = scope, stop: threading.Event = stop, held: Any = held) -> None:
                    try:
                        with use_instance(scope):
                            poll_forever(token, self.handle, stop)
                    finally:
                        held.close()  # frees the bot for another process

                thread = threading.Thread(target=run, name=f"telegram-{bot_id(token)}", daemon=True)
                thread.start()
                self._bots[token] = (scope, stop, thread)
            return sorted(bot_id(t) for t in self._bots)

    def ensure_slack_apps(self, apps: dict[str, tuple[Any, str, str]]) -> list[str]:
        """Exactly one Socket Mode connection per app in `apps` (app token -> (instance scope, bot
        token, team id)). Returns the "<team>/<app>" keys connected here."""
        import hashlib

        from superaide.channels import health
        from superaide.channels.slack import auth_error, socket_mode_handler
        from superaide.platform.tenancy.context import use_instance
        from superaide.platform.tenancy.endpoints import slack_key

        with self._lock:
            for app, (scope, held) in list(self._apps.items()):
                if app not in apps or apps[app][0] != scope:
                    try:
                        held.close()
                    except Exception:
                        log.exception("closing a Slack listener")
                    del self._apps[app]
            for app, (scope, bot, team) in apps.items():
                if app in self._apps:
                    continue
                lock = claim_listener(f"slack:{hashlib.sha256(app.encode()).hexdigest()[:16]}")
                if lock is None:
                    log.info("Slack app %s listens in another process; skipped here", slack_key(team, app))
                    continue

                def in_scope(msg: InboundMessage, scope: Any = scope) -> Any:
                    with use_instance(scope):  # Slack calls back on its own threads: say whose it is
                        return self.handle(msg)

                with use_instance(scope):
                    try:
                        handler = socket_mode_handler(bot, app, team, in_scope)
                        handler.connect()
                    except Exception as exc:
                        lock.close()
                        log.exception("Slack listener failed to start")
                        if code := auth_error(exc):
                            health.record("slack", health.AUTH_FAILED, f"Slack rejected the token ({code})")
                        continue
                    health.record("slack", health.OK)
                self._apps[app] = (scope, _Held(handler, lock))
            return sorted(slack_key(apps[a][2], a) for a in self._apps if a in apps)

    def stop(self) -> None:
        with self._lock:
            self._ensure_telegram("")
            self._ensure_slack("", "", "")
            for _, stop, _ in self._bots.values():
                stop.set()
            self._bots.clear()
            for _, held in self._apps.values():
                try:
                    held.close()
                except Exception:
                    log.exception("closing a Slack listener")
            self._apps.clear()

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
            import hashlib

            from superaide.channels import health
            from superaide.channels.slack import auth_error, socket_mode_handler

            held = claim_listener(f"slack:{hashlib.sha256(app.encode()).hexdigest()[:16]}")
            if held is None:
                log.info("this Slack app listens in another process; skipped here")
                return
            try:
                handler = socket_mode_handler(bot, app, team, self.handle)
                handler.connect()
                self._slack = (key, _Held(handler, held))
            except Exception as exc:
                held.close()
                log.exception("Slack listener failed to start")
                code = auth_error(exc)
                if code:
                    health.record("slack", health.AUTH_FAILED, f"Slack rejected the token ({code})")
            else:
                health.record("slack", health.OK)


class _Held:
    """A Slack handler plus the listener lock it holds; closing one closes both."""

    def __init__(self, handler: Any, held: Any) -> None:
        self.handler, self.held = handler, held

    def close(self) -> None:
        try:
            self.handler.close()
        finally:
            self.held.close()
