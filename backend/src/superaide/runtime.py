"""The running agents and channel listeners, shared by the API process and the worker (ADR 0009 T11).

- `get_agent()`: the agent of the instance in scope, built from that instance's own connections
  and rebuilt when they change (one per hired Ava, least recently used dropped).
- `apply_connections()`: start, restart or stop the listeners to match every instance's saved
  connections: one poller per owner bot (or one webhook each), and Slack.

In development one process does everything (`superaide serve`, as start.command runs it). On a
server the API serves requests and a separate `superaide worker` runs the jobs and the listeners
(RUN_JOBS_IN_API / RUN_CHANNELS_IN_API off in the API). Each bot is polled by one process only:
a poller holds a Postgres advisory lock for its bot (channels/runner.py), so a second worker,
or an API with listeners left on, skips it.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

from superaide.channels import telegram
from superaide.channels.base import InboundMessage
from superaide.channels.runner import ChannelRunner
from superaide.config import Settings
from superaide.db.session import session_scope
from superaide.pipeline import Agent
from superaide.tenancy.context import current_scope, default_scope, scope_or_default, use_instance
from superaide.tenancy.endpoints import owner_of, sync_telegram, webhook_secret

log = logging.getLogger(__name__)
MAX_CACHED = 256  # agents kept built, one per recently active instance


class Runtime:
    def __init__(self, settings: Settings, fixed: Agent | None = None) -> None:
        self.settings = settings
        self.fixed = fixed  # tests pass their own agent; it is never rebuilt
        self._agents: OrderedDict[Any, tuple[Agent, str]] = OrderedDict()
        self._lock = threading.Lock()
        self.runner = ChannelRunner(self.handle_listened)
        self._hooked: dict[str, Any] = {}  # bot token -> instance whose webhook Telegram already has

    # --- agents ------------------------------------------------------------------------------

    def get_agent(self) -> Agent:
        """The agent of the instance in scope, rebuilt whenever its connections change in the app."""
        if self.fixed is not None:
            return self.fixed
        from superaide.factory import build_agent
        from superaide.vault.store import runtime_settings, version

        key = scope_or_default().agent_id
        with session_scope() as s:
            v = version(s)
        with self._lock:
            cached = self._agents.get(key)
            if cached is not None and cached[1] == v:
                self._agents.move_to_end(key)
                return cached[0]
        built = build_agent(runtime_settings(self.settings))
        with self._lock:
            self._agents[key] = (built, v)
            self._agents.move_to_end(key)
            while len(self._agents) > MAX_CACHED:
                self._agents.popitem(last=False)
        return built

    def handle_on_server_channels(self, msg: InboundMessage) -> Any:
        """Slack (one app per server for now) and the legacy Telegram webhook: the server's first instance."""
        with use_instance(default_scope()):
            return self.get_agent().handle(msg)

    def handle_listened(self, msg: InboundMessage) -> Any:
        """From a listener: an owner bot's poller runs inside its owner's instance (ADR 0009 T4)."""
        if current_scope() is not None:
            return self.get_agent().handle(msg)
        return self.handle_on_server_channels(msg)

    # --- listeners -----------------------------------------------------------------------------

    def apply_connections(self, *, listen: bool) -> dict[str, bool]:
        """Use new or removed credentials now. With `listen` off (an API whose worker listens),
        only the agent of the instance in scope is rebuilt."""
        if self.fixed is not None or not listen:
            self.get_agent()
            return {}
        with use_instance(default_scope()):  # Slack: one app per server, the first instance's
            started = self.runner.ensure(self.get_agent().settings.model_copy(update={"telegram_bot_token": ""}))
        bots = sync_telegram()  # each owner's own bot, registered to their instance
        base = self.settings.telegram_webhook_base_url
        if base:
            self.runner.ensure_bots({})
            for token, scope in bots.items():
                if self._hooked.get(token) != scope:
                    with use_instance(scope), session_scope() as s:
                        endpoint = owner_of(s, "telegram", telegram.bot_id(token))
                    if endpoint is not None and telegram.set_webhook(
                        token, f"{base.rstrip('/')}/webhooks/telegram/{endpoint.id}", webhook_secret(endpoint.id)
                    ):
                        self._hooked[token] = scope
            started["telegram"] = bool(bots)
        else:
            started["telegram"] = bool(self.runner.ensure_bots(bots))
        return started

    def stop(self) -> None:
        self.runner.stop()
