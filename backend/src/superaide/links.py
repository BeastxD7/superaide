"""The address links in emails point at: the one the person is using, if it's one of ours.

People reach the app at FRONTEND_ORIGIN, but also at a public tunnel, a LAN address (to try it
on a phone) or the server's public name. A link built from FRONTEND_ORIGIN alone breaks for
them. So a link uses the request's own origin (the browser's Origin header, or the host the web
app's proxy saw), but only when that origin is allowed: FRONTEND_ORIGIN (as localhost or
127.0.0.1), PUBLIC_BASE_URL, LINK_ORIGINS (patterns such as https://*.trycloudflare.com) and
DEV_ORIGINS hosts. Anything else, including a forged Host header, falls back to PUBLIC_BASE_URL,
else FRONTEND_ORIGIN: a link in our email can never point at someone else's site.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from urllib.parse import urlsplit

from fastapi import Request

from superaide.config import Settings, get_settings


def _parts(origin: str) -> tuple[str, str] | None:
    """(scheme, host[:port]) of a bare origin, or None if it isn't one."""
    try:
        u = urlsplit(origin.strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        return None
    if u.path not in ("", "/") or u.query or u.fragment:
        return None
    try:
        port = u.port
    except ValueError:
        return None
    host = u.hostname.lower()
    return u.scheme, f"{host}:{port}" if port else host


def _allowed(settings: Settings) -> tuple[set[tuple[str, str]], list[tuple[str, str]]]:
    exact: set[tuple[str, str]] = set()
    for origin in (
        settings.frontend_origin,
        settings.frontend_origin.replace("localhost", "127.0.0.1"),
        settings.frontend_origin.replace("127.0.0.1", "localhost"),
        settings.public_base_url,
    ):
        if origin and (p := _parts(origin)):
            exact.add(p)
    patterns: list[tuple[str, str]] = []
    for item in settings.link_origins.split(","):
        item = item.strip().lower()
        if "://" in item:
            scheme, host = item.split("://", 1)
            if scheme in ("http", "https") and host and "/" not in host:
                patterns.append((scheme, host))
    for host in settings.dev_origins.split(","):  # host names only (Next's allowedDevOrigins)
        host = host.strip().lower()
        if host and "/" not in host:
            patterns += [("http", host), ("http", f"{host}:*"), ("https", host), ("https", f"{host}:*")]
    return exact, patterns


def origin_allowed(origin: str, settings: Settings | None = None) -> bool:
    p = _parts(origin)
    if p is None:
        return False
    exact, patterns = _allowed(settings or get_settings())
    return p in exact or any(p[0] == scheme and fnmatchcase(p[1], host) for scheme, host in patterns)


def request_origin(request: Request, settings: Settings | None = None) -> str:
    """Where this person is using the app (if allowed), else the server's own address."""
    settings = settings or get_settings()
    candidates = [request.headers.get("origin") or ""]
    host = request.headers.get("x-forwarded-host") or ""
    proto = request.headers.get("x-forwarded-proto") or ""
    if host and proto in ("http", "https"):
        candidates.append(f"{proto}://{host}")
    for candidate in candidates:
        if candidate and origin_allowed(candidate, settings):
            scheme, host_port = _parts(candidate)  # type: ignore[misc]
            return f"{scheme}://{host_port}"
    return (settings.public_base_url or settings.frontend_origin).rstrip("/")
