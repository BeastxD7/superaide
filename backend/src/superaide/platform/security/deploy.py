"""Startup check for a deployment that shares one server's credentials between many owners (issue #82).

In self_hosted mode the server's own ANTHROPIC_API_KEY and Telegram/Slack tokens from .env serve every
instance, and owner-typed provider addresses may point at private ranges. That is right for one person's own
machine and wrong for a public server where anyone can sign up. The production compose sets DEPLOY_MODE=hosted;
this catches the forgotten setting."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from superaide.platform.config import Settings, get_settings

log = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0", ""}


class UnsafeDeployment(RuntimeError):
    pass


def _server_credentials(s: Settings) -> bool:
    return any((s.anthropic_api_key, s.telegram_bot_token, s.slack_bot_token, s.slack_app_token))


def _public(s: Settings) -> bool:
    """The app is reachable from beyond this machine: a non-local address is configured, or cookies are
    HTTPS-only (which only works behind a real public address)."""
    if s.session_cookie_secure:
        return True
    for url in (s.public_base_url, s.frontend_origin):
        host = (urlsplit(url).hostname or "").lower()
        if host and host not in _LOCAL_HOSTS and not host.endswith(".local"):
            return True
    return False


def problem(s: Settings) -> str | None:
    if s.deploy_mode == "hosted" or not s.allow_many_owners or not _server_credentials(s):
        return None
    return (
        "DEPLOY_MODE is self_hosted, ALLOW_MANY_OWNERS is on and this server has its own AI or bot credentials in "
        ".env, so anyone who signs up would run on them and could point a provider at private addresses. "
        "Set DEPLOY_MODE=hosted (each owner then brings their own keys), or set ALLOW_MANY_OWNERS=false."
    )


def check(settings: Settings | None = None) -> None:
    """Warn on a development machine; refuse to start when the address looks public."""
    s = settings or get_settings()
    msg = problem(s)
    if msg is None:
        return
    if _public(s) and not s.allow_unsafe_public_self_hosted:
        raise UnsafeDeployment(msg + " (ALLOW_UNSAFE_PUBLIC_SELF_HOSTED=true overrides this check.)")
    log.warning(msg)
