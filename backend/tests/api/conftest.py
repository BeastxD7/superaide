"""Shared helpers for the HTTP API tests: browsers, accounts and the owner who hired Ava.

Every browser here keeps its own cookie jar and reports server errors as a 500
response instead of raising, so a test can assert "this never becomes a 500".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superaide.main import create_app
from tests.test_connections import master_key  # noqa: F401  (autouse: a test vault key, never a key file in ~)
from tests.test_intro import brain  # noqa: F401  (the fake Claude that reads facts out of shared text)

PASSWORD = "correct horse battery"
OWNER = {"name": "Shashank Rao", "email": "shashank@example.com", "password": PASSWORD}
OTHER = {"name": "Priya Tan", "email": "priya@example.com", "password": PASSWORD}
CSRF = {"X-Requested-With": "superaide"}

Browser = Callable[..., TestClient]


@pytest.fixture
def app(agent) -> FastAPI:
    return create_app(agent)


@pytest.fixture
def browser(app) -> Browser:
    """Make a fresh browser talking to the same app. `ip` sets the client address the server sees."""

    def make(*, csrf: bool = True, ip: str = "testclient") -> TestClient:
        return TestClient(app, headers=CSRF if csrf else None, raise_server_exceptions=False, client=(ip, 50000))

    return make


def signup(client: TestClient, **fields: Any) -> httpx.Response:
    body = {k: fields.get(k, OWNER[k]) for k in ("name", "email", "password")}
    return client.post("/auth/signup", json=body)


def login(client: TestClient, email: str = OWNER["email"], password: str = PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"email": email, "password": password})


def hire(client: TestClient, agent: str = "ava", accepted: Any = True) -> httpx.Response:
    return client.post("/app/hire", json={"agent": agent, "accepted_notice": accepted})


@pytest.fixture
def owner(browser) -> TestClient:
    """Signed in as the account that hired Ava."""
    c = browser()
    assert signup(c).status_code == 200
    assert hire(c).status_code == 200
    return c


@pytest.fixture
def other(browser, owner) -> TestClient:
    """Signed in, but not the owner: Ava already works for someone else."""
    c = browser()
    assert signup(c, **OTHER).status_code == 200
    return c


def app_routes(app: FastAPI, prefix: str) -> list[tuple[str, str]]:
    """(METHOD, path) for every operation under `prefix`, read from the app's own route table
    (its OpenAPI description), so a newly added endpoint is covered without editing the tests."""
    out = []
    for path, operations in app.openapi()["paths"].items():
        if path == prefix or path.startswith(prefix + "/"):
            out.extend((method.upper(), path) for method in operations)
    return sorted(out)


def fill_path(path: str) -> str:
    """A concrete URL for a templated path; IDs are random so they match nothing."""
    return (
        path.replace("{document_id}", "6f1f5b8e-0000-4000-8000-000000000001")
        .replace("{memory_id}", "6f1f5b8e-0000-4000-8000-000000000002")
        .replace("{identity_id}", "6f1f5b8e-0000-4000-8000-000000000003")
        .replace("{provider}", "telegram")
    )
