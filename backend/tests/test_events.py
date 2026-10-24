"""The in-process event bus (ADR 0015): synchronous, in subscription order, errors reach the publisher."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from superaide.contracts.events import ApprovalDecided, EventBus, bus


@dataclass(frozen=True)
class Ping:
    n: int


@dataclass(frozen=True)
class Pong:
    n: int


def test_handlers_run_in_subscription_order_and_only_for_their_event():
    local, seen = EventBus(), []
    local.subscribe(Ping, lambda e: seen.append(("first", e.n)))
    local.subscribe(Ping, lambda e: seen.append(("second", e.n)))
    local.subscribe(Pong, lambda e: seen.append(("pong", e.n)))
    local.publish(Ping(1))
    assert seen == [("first", 1), ("second", 1)]


def test_publishing_with_no_subscriber_does_nothing():
    EventBus().publish(Ping(1))


def test_subscribing_the_same_handler_twice_runs_it_once():
    local, seen = EventBus(), []

    def handler(event: Ping) -> None:
        seen.append(event.n)

    local.subscribe(Ping, handler)
    local.subscribe(Ping, handler)
    local.publish(Ping(7))
    assert seen == [7]


def test_a_failing_handler_reaches_the_publisher_like_the_direct_call_did():
    local = EventBus()

    def boom(event: Ping) -> None:
        raise RuntimeError("nope")

    local.subscribe(Ping, boom)
    with pytest.raises(RuntimeError, match="nope"):
        local.publish(Ping(1))


def test_the_plugins_subscribe_the_receipts_to_decided_approvals():
    import superaide.plugins  # noqa: F401

    assert bus._handlers[ApprovalDecided], "skills.receipts must listen for decisions once the plugins are loaded"


def test_the_plugins_register_what_the_channel_code_looks_up():
    import superaide.plugins  # noqa: F401
    from superaide.channels.base import MESSENGER_FACTORIES, PRESS_HANDLERS

    assert {"telegram", "slack"} <= set(MESSENGER_FACTORIES)
    assert "telegram" in PRESS_HANDLERS
