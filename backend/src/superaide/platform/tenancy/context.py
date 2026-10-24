"""Which agent instance the current work is for (ADR 0009).

Every entry point (a signed-in request, a channel webhook, a job, the CLI) sets the
scope before it touches data; everything below reads it from here. A context
variable follows the work across `await`s and stays separate per thread, so two
requests or two jobs can never see each other's scope.

Today nothing requires a scope yet: `scope_or_default()` falls back to the single
development instance from settings. Work package T1 makes a missing scope an error
and backs it with Row-Level Security in Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from superaide.platform.config import get_settings


@dataclass(frozen=True)
class InstanceScope:
    """One hire of an agent: `agent_id` is the isolation boundary, `tenant_id` the customer."""

    tenant_id: uuid.UUID
    agent_id: uuid.UUID


class NoScope(LookupError):
    """Data was touched with no agent instance chosen."""


_current: ContextVar[InstanceScope | None] = ContextVar("superaide_instance_scope", default=None)
_everywhere: ContextVar[bool] = ContextVar("superaide_all_instances", default=False)


def current_scope() -> InstanceScope | None:
    return _current.get()


def require_scope() -> InstanceScope:
    scope = _current.get()
    if scope is None:
        raise NoScope("no agent instance is in scope")
    return scope


def default_scope() -> InstanceScope:
    """The single development instance (the ids every existing row already carries)."""
    settings = get_settings()
    return InstanceScope(tenant_id=settings.tenant_id, agent_id=settings.agent_id)


def scope_or_default() -> InstanceScope:
    return _current.get() or default_scope()


@contextmanager
def use_instance(scope: InstanceScope) -> Iterator[InstanceScope]:
    """Run the block for this instance; the previous scope comes back afterwards, even on error."""
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def mine(model: Any) -> Any:
    """`model.agent_id == <the instance in scope>`: the explicit filter every per-instance query adds,
    in front of Row-Level Security (which a superuser connection skips)."""
    return model.agent_id == scope_or_default().agent_id


@contextmanager
def all_instances() -> Iterator[None]:
    """Platform code (operator views, migrations' helpers, tests) that reads across instances on
    purpose: the automatic per-instance filter (db/session.py) is off inside. Row-Level Security
    still applies to the app's own database role."""
    token = _everywhere.set(True)
    try:
        yield
    finally:
        _everywhere.reset(token)


def across_instances() -> bool:
    return _everywhere.get()
