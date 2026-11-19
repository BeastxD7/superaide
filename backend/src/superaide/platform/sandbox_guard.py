"""The switch that stops code at the edge of the database while a sandbox preview runs (#499, sandbox.py).

Lives in platform/ (importable by everyone, imports nothing of ours) so `proactive.notify` and the
escalation code can ask `active()` without reaching into reception, which would close a cycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class Capture:
    """What the turn would have done outside the database."""

    notifications: list[dict[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


_active: ContextVar[Capture | None] = ContextVar("reception_sandbox", default=None)


def active() -> Capture | None:
    return _active.get()


@contextmanager
def sandboxed() -> Iterator[Capture]:
    capture = Capture()
    token = _active.set(capture)
    try:
        yield capture
    finally:
        _active.reset(token)
