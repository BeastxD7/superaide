"""Fixtures for the MCP client tests: a fake server in this process, never the network."""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace
from typing import Any

import pytest

from superaide.app.accounts import owner as hired_owner  # noqa: F401  (used by loop tests)
from superaide.integrations.mcp import service, transport
from superaide.platform.db.session import session_scope
from superaide.platform.security import urls
from tests.api_support import *  # noqa: F403  (the app and browser fixtures, a fast password hash)
from tests.app.test_connections import master_key  # noqa: F401  (autouse: a test vault key)
from tests.integrations.mcp import fake_server


@pytest.fixture(autouse=True)
def no_dns(monkeypatch) -> None:
    """Names resolve to a fixed public address (literals to themselves, localhost to loopback): no lookups in tests."""

    def resolve(host: str):
        if host == "localhost":
            return [ipaddress.ip_address("127.0.0.1")]
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(urls, "resolve", resolve)


@pytest.fixture
def calls(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    """Every tool call the fake server actually ran."""
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(transport, "FACTORY", fake_server.in_process(seen))
    return seen


def sender(person, relation: str = "manager") -> Any:
    return SimpleNamespace(person=person, relation=relation, trust=3, name=person.display_name)


@pytest.fixture
def server_id(calls, manager) -> str:
    """One server added, listed and approved as listed (every tool still `write` until the owner classifies it)."""
    with session_scope() as s:
        server = service.create(s, label="Notes", kind="http", url="https://notes.example.com/mcp")
        service.refresh(s, server)
        service.approve(s, server)
        return str(server.id)
