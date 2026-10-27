"""An in-process event bus: "after X happened, whoever cares does Y" without X knowing who cares.

ADR 0015 (technique 4). A module that emits an event imports nothing from the modules that react to it; the
reacting module subscribes at import. Delivery is synchronous, in subscription order, on the caller's thread and
inside the caller's transaction, so replacing a direct call with `publish` changes no behaviour. A handler that
raises propagates to the publisher, exactly as the direct call did.

Subscribers register when their module is imported, and `superaide.plugins` is what imports them at startup.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from superaide.approvals.models import Approval
from superaide.memory.models import Event

E = TypeVar("E")


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        """Register `handler` once; subscribing the same function twice (a module imported again) is a no-op."""
        handlers = self._handlers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    def publish(self, event: object) -> None:
        for handler in list(self._handlers.get(type(event), ())):
            handler(event)


bus = EventBus()


@dataclass(frozen=True)
class ApprovalDecided:
    """The owner's yes or no on an approval was recorded (and, for a yes, carried out when it could be)."""

    session: Session
    approval: Approval
    approved: bool


@dataclass(frozen=True)
class SkillRemoved:
    """The owner removed a skill; whatever was learned from its source goes with it (knowledge.connector_facts)."""

    session: Session
    skill_id: str


@dataclass(frozen=True)
class CalendarEventsRead:
    """Ava read these Google Calendar events for the owner (skills.calendar)."""

    session: Session
    events: list[Any]


@dataclass(frozen=True)
class MailIngested:
    """One email was logged as an inbound event (skills.gmail_inbox)."""

    session: Session
    event: Event
