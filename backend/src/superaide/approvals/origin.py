"""Where an approval card was asked for (#642): the owner's conversation and the message that asked.

The owner's chat turn binds its conversation here while it runs; `binding.seal` copies it into the card's payload
before the card is sealed, so the origin is part of what the seal covers and nothing can move a card to another
conversation afterwards. The model never sees or sets these values: they come from the turn's own inbound event.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

KEYS = ("origin_context_id", "origin_event_id", "origin_provider")
_current: ContextVar[dict[str, str] | None] = ContextVar("approval_origin", default=None)


@contextmanager
def bound(*, context_id: Any, event_id: Any, provider: str) -> Iterator[None]:
    token = _current.set(
        {"origin_context_id": str(context_id), "origin_event_id": str(event_id), "origin_provider": provider}
    )
    try:
        yield
    finally:
        _current.reset(token)


def stamp(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload with the origin of the running turn added (never overwriting one already there)."""
    origin = _current.get()
    if not origin:
        return payload
    return {**origin, **payload}
