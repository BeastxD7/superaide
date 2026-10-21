"""Engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, cast

from sqlalchemy import Connection, CursorResult, Engine, Result, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from superaide.config import get_settings
from superaide.db.rls import set_scope_sql
from superaide.tenancy.context import scope_or_default


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _scope_transaction(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    """Tell Postgres which agent instance this transaction is for, so Row-Level Security
    (db/rls.py) shows only that instance's rows. Transaction-local: a pooled connection
    never carries one request's instance into the next."""
    scope = scope_or_default()  # T2 onwards: every entry point sets the scope; until then, the dev instance
    connection.execute(text(set_scope_sql()), {"agent": str(scope.agent_id), "tenant": str(scope.tenant_id)})


def rescope(session: Session) -> None:
    """Re-tell Postgres the instance in scope, after switching instances inside one transaction."""
    scope = scope_or_default()
    session.execute(text(set_scope_sql()), {"agent": str(scope.agent_id), "tenant": str(scope.tenant_id)})


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
