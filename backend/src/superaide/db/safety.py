"""Refuse to wipe a database that is not disposable.

Every destructive helper (the eval harness's `reset_db`, the `reset-db` command, the test suite's
truncate and drop fixtures) calls `assert_disposable_database(url)` before it runs a statement.

A database passes only when BOTH hold:
  1. its name contains one of the markers (default `test`, `eval`, `scratch`, `e2e`; set
     DISPOSABLE_DB_MARKERS to a comma-separated list to change them), and
  2. it is not the live database, i.e. not the DATABASE_URL (or MIGRATION_DATABASE_URL) in
     backend/.env or the LIVE_DATABASE_URL environment variable, compared after normalising the
     driver, the host (localhost, 127.0.0.1, ::1), the default port and the name's case.

Incident 2026-10-24: the harness was imported directly with only TEST_DATABASE_URL exported, the
engine resolved DATABASE_URL from backend/.env, and `reset_db()` truncated the live database.

This module imports nothing from the app (no settings), so conftest can load it before the
environment is prepared.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy.engine import URL, make_url

DEFAULT_MARKERS = ("test", "eval", "scratch", "e2e")
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}
_WORKER_SUFFIX = re.compile(r"_gw\d+$")  # pytest-xdist: <name>_gw0


class UnsafeDatabase(RuntimeError):
    """Raised instead of running a destructive statement against a database that may be live."""


def markers() -> tuple[str, ...]:
    raw = os.environ.get("DISPOSABLE_DB_MARKERS", "")
    found = tuple(m.strip().lower() for m in raw.split(",") if m.strip())
    return found or DEFAULT_MARKERS


def _parse(url: str | URL) -> URL:
    return url if isinstance(url, URL) else make_url(str(url).strip())


def identity(url: str | URL) -> tuple[str, int, str]:
    """(host, port, database) with the driver, user, password and query dropped, host and port
    defaulted and the name lower-cased and stripped of an xdist worker suffix."""
    u = _parse(url)
    host = (u.host or "").lower()
    if host in _LOCAL_HOSTS:
        host = "localhost"
    name = _WORKER_SUFFIX.sub("", (u.database or "").lower())
    return host, u.port or 5432, name


def database_name(url: str | URL) -> str:
    return _parse(url).database or ""


def live_urls(extra: tuple[str, ...] = ()) -> list[str]:
    """The URLs that must never be wiped: backend/.env and LIVE_DATABASE_URL (and `extra`)."""
    found: list[str] = list(extra)
    try:
        from dotenv import dotenv_values

        values = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
        found += [str(values[k]) for k in ("DATABASE_URL", "MIGRATION_DATABASE_URL") if values.get(k)]
    except Exception:  # an unreadable .env must not disable the name check below
        pass
    if os.environ.get("LIVE_DATABASE_URL"):
        found.append(os.environ["LIVE_DATABASE_URL"])
    return found


def is_live(url: str | URL, extra: tuple[str, ...] = ()) -> bool:
    target = identity(url)
    for live in live_urls(extra):
        try:
            if identity(live) == target:
                return True
        except Exception:  # an unparsable live URL: ignore it, the name check still applies
            continue
    return False


def has_marker(url: str | URL) -> bool:
    name = database_name(url).lower()
    return any(m in name for m in markers())


def assert_disposable_database(url: str | URL, *, extra_live: tuple[str, ...] = ()) -> None:
    """Raise UnsafeDatabase unless `url` is a database that may be wiped."""
    try:
        name = database_name(url)
        marked = has_marker(url)
        live = is_live(url, extra_live)
    except Exception as exc:  # cannot tell what this is: refuse
        raise UnsafeDatabase(f"Refusing to wipe a database whose URL cannot be read ({type(exc).__name__})") from exc
    if live:
        raise UnsafeDatabase(f"Refusing to wipe {name!r}: it is the configured live database (DATABASE_URL).")
    if not marked:
        raise UnsafeDatabase(
            f"Refusing to wipe {name!r}: a disposable database has one of {', '.join(markers())} in its name. "
            "Pass an explicit scratch database (TEST_DATABASE_URL / EVAL_DATABASE_URL)."
        )
