"""Engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, Result, create_engine
from sqlalchemy.orm import Session, sessionmaker

from superaide.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """A transaction: commits on success, rolls back on error."""
    session = _factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class NotFound(LookupError):
    """A row that must exist (it was just checked, or is referenced) is missing."""


def require[M](session: Session, model: type[M], ident: Any) -> M:
    """Like session.get, but raises NotFound instead of returning None."""
    row = session.get(model, ident)
    if row is None:
        raise NotFound(f"{model.__name__} {ident} not found")
    return row


def rows_affected(result: Result[Any]) -> int:
    """Rows changed by an UPDATE or DELETE (execute() is typed as a plain Result)."""
    return cast(CursorResult[Any], result).rowcount or 0


EXTENSIONS = ("pg_trgm",)  # typo-tolerant recall; optional: recall works without it


def ensure_extensions(engine: Engine) -> None:
    """Install the optional Postgres extensions the server has; skip any it lacks."""
    from sqlalchemy import text

    with engine.begin() as conn:
        for name in EXTENSIONS:
            available = conn.scalar(text("SELECT count(*) FROM pg_available_extensions WHERE name = :n"), {"n": name})
            if available:
                conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{name}"'))
