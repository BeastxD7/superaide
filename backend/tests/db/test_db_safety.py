"""The guard that stops a wipe of the live database (incident 2026-10-24)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from superaide.db import safety
from superaide.db.safety import UnsafeDatabase, assert_disposable_database
from superaide.evals import harness

LIVE = "postgresql+psycopg://agent:agent@localhost:5432/agent"


@pytest.fixture
def live_env(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"DATABASE_URL={LIVE}\n")
    monkeypatch.setattr(safety, "ENV_FILE", env)
    monkeypatch.delenv("DISPOSABLE_DB_MARKERS", raising=False)
    monkeypatch.delenv("LIVE_DATABASE_URL", raising=False)


@pytest.mark.parametrize(
    "url",
    [
        LIVE,
        "postgresql+psycopg://agent:agent@127.0.0.1:5432/agent",
        "postgresql://agent:agent@localhost/agent",
        "postgresql+psycopg2://other:pw@[::1]:5432/agent",
        "postgresql+psycopg://agent:agent@LOCALHOST:5432/AGENT",
        "postgresql://agent@/agent",
        "postgresql+psycopg://agent:agent@localhost:5432/agent?sslmode=disable",
    ],
)
def test_the_live_database_is_refused_however_the_url_is_spelled(live_env, url):
    with pytest.raises(UnsafeDatabase):
        assert_disposable_database(url)


def test_a_live_url_that_looks_disposable_is_still_refused(live_env, monkeypatch):
    monkeypatch.setenv("LIVE_DATABASE_URL", "postgresql://agent:agent@127.0.0.1/agent_test")
    with pytest.raises(UnsafeDatabase, match="live"):
        assert_disposable_database("postgresql+psycopg://agent:agent@localhost:5432/AGENT_TEST")


def test_a_database_with_no_marker_is_refused_even_when_it_is_not_the_live_one(live_env):
    with pytest.raises(UnsafeDatabase, match="disposable"):
        assert_disposable_database("postgresql+psycopg://agent:agent@localhost:5544/production")


def test_a_different_server_with_the_same_name_is_not_the_live_database_but_still_needs_a_marker(live_env):
    with pytest.raises(UnsafeDatabase):
        assert_disposable_database("postgresql://agent:agent@db.internal:5432/agent")


@pytest.mark.parametrize(
    "name",
    [
        "agent_test",
        "agent_test_dbguard",
        "agent_eval",
        "agent_test_evals",
        "scratch_1",
        "superaide_e2e_3",
        "agent_test_gw0",
    ],
)
def test_disposable_names_are_allowed(live_env, name):
    assert_disposable_database(f"postgresql+psycopg://agent:agent@localhost:5432/{name}")


def test_the_marker_list_is_configurable_and_cannot_be_emptied(live_env, monkeypatch):
    monkeypatch.setenv("DISPOSABLE_DB_MARKERS", "sandbox")
    assert_disposable_database("postgresql://a:b@localhost/my_sandbox")
    with pytest.raises(UnsafeDatabase):
        assert_disposable_database("postgresql://a:b@localhost/agent_test")
    monkeypatch.setenv("DISPOSABLE_DB_MARKERS", " , ")
    assert_disposable_database("postgresql://a:b@localhost/agent_test")  # blank falls back to the defaults


def test_the_worker_suffix_does_not_hide_the_live_database(live_env):
    with pytest.raises(UnsafeDatabase):
        assert_disposable_database("postgresql://agent:agent@localhost/agent_gw0")


def test_an_unreadable_url_is_refused(live_env):
    with pytest.raises(UnsafeDatabase):
        assert_disposable_database("not a url")


def test_without_a_database_name_it_is_refused(live_env):
    with pytest.raises(UnsafeDatabase):
        assert_disposable_database("postgresql://agent:agent@localhost:5432/")


class _Boom(AssertionError):
    pass


class _FakeEngine:
    def __init__(self, url: str) -> None:
        self.url = make_url(url)
        self.statements: list[str] = []

    def begin(self):
        raise _Boom("a connection was opened")

    def connect(self):
        raise _Boom("a connection was opened")


def test_reset_db_raises_before_running_any_statement_on_the_live_database(live_env, monkeypatch):
    engine = _FakeEngine("postgresql+psycopg://agent:agent@127.0.0.1:5432/agent")
    monkeypatch.setattr(harness, "get_engine", lambda *a, **k: engine)
    monkeypatch.setattr(harness, "ensure_extensions", lambda e: pytest.fail("touched the database"))
    with pytest.raises(UnsafeDatabase):
        harness.reset_db()
    with pytest.raises(UnsafeDatabase):
        harness.reset_db(wipe_ledger=True)


def test_reset_db_refuses_an_unmarked_database_too(live_env, monkeypatch):
    engine = _FakeEngine("postgresql+psycopg://agent:agent@localhost:5432/people")
    monkeypatch.setattr(harness, "get_engine", lambda *a, **k: engine)
    with pytest.raises(UnsafeDatabase):
        harness.reset_db()


def test_reset_db_goes_on_to_the_database_for_a_disposable_one(live_env, monkeypatch):
    engine = _FakeEngine("postgresql+psycopg://agent:agent@localhost:5432/agent_eval")
    monkeypatch.setattr(harness, "get_engine", lambda *a, **k: engine)
    monkeypatch.setattr(harness, "ensure_extensions", lambda e: (_ for _ in ()).throw(_Boom("past the guard")))
    with pytest.raises(_Boom, match="past the guard"):
        harness.reset_db()
