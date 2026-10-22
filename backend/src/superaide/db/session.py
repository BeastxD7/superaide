"""Engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, cast

from sqlalchemy import Connection, CursorResult, Engine, Result, create_engine, event, text
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction, sessionmaker, with_loader_criteria

from superaide.config import get_settings
from superaide.db.rls import set_scope_sql
from superaide.tenancy.context import across_instances, current_scope, scope_or_default

# Every connection speaks UTC, so a timestamp read back and compared as text matches the one written,
# whatever time zone the database server is set to (a deferred escalation compared isoformat strings).
CONNECT_ARGS = {"options": "-c timezone=UTC"}


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(
        url or settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        connect_args=CONNECT_ARGS,
    )


@lru_cache
def lock_engine() -> Engine:
    """Connections that each hold one long-lived advisory lock (a listener's), outside the pool."""
    from sqlalchemy.pool import NullPool

    return create_engine(get_settings().database_url, poolclass=NullPool, connect_args=CONNECT_ARGS)


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


def _per_instance_classes() -> list[type]:
    """Every mapped class whose rows belong to one agent instance (it has agent_id), except the
    routing tables read before an instance is known (db/rls.py EXEMPT)."""
    from superaide.db.models import Base
    from superaide.db.rls import EXEMPT

    return [
        m.class_
        for m in Base.registry.mappers
        if "agent_id" in m.columns and getattr(m.local_table, "name", "") not in EXEMPT
    ]


@event.listens_for(Session, "do_orm_execute")
def _only_this_instance(state: ORMExecuteState) -> None:
    """The first line of tenancy (ADR 0009 T7): while an instance is in scope (every owner request,
    job, poller and webhook), every ORM read, update and delete of per-instance rows gets
    `agent_id = <that instance>`, joins and relationships included, so a query that forgot to filter
    can't see another owner's rows. Row-Level Security is the second line."""
    if not (state.is_select or state.is_update or state.is_delete):
        return
    scope = current_scope()
    if scope is None or across_instances() or state.execution_options.get("all_instances"):
        return  # nothing chosen (platform code, the operator, the server's own channels use the default)
    agent = scope.agent_id
    state.statement = state.statement.options(
        *(with_loader_criteria(cls, lambda c: c.agent_id == agent, include_aliases=True) for cls in _classes())
    )


@lru_cache
def _classes_cached() -> tuple[type, ...]:
    return tuple(_per_instance_classes())


def _classes() -> tuple[type, ...]:
    return _classes_cached()


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
