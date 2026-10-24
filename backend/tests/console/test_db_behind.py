"""The start-up check that the database is at the migration the code expects (security/dbcheck.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from superaide.main import create_app
from superaide.platform.config import get_settings
from superaide.platform.security import dbcheck


def fake(monkeypatch, *, applied, heads):
    monkeypatch.setattr(dbcheck, "read_applied", lambda: applied)
    monkeypatch.setattr(dbcheck, "code_heads", lambda: heads)


def test_a_database_at_the_code_head_is_fine(monkeypatch):
    fake(monkeypatch, applied=("0040",), heads=("0040",))
    assert dbcheck.status().state == "ok"
    assert dbcheck.check(get_settings()) == ""


def test_a_database_behind_the_code_is_named_with_the_fix(monkeypatch):
    fake(monkeypatch, applied=("0039",), heads=("0040",))
    st = dbcheck.status()
    assert st.state == "behind"
    assert "0039" in st.message and "0040" in st.message and "alembic upgrade head" in st.message


def test_a_database_without_alembic_is_skipped(monkeypatch):
    fake(monkeypatch, applied=None, heads=("0040",))  # built by init-db or by the test suite
    assert dbcheck.status().state == "unmanaged"
    assert dbcheck.check(get_settings()) == ""


def test_two_heads_in_the_code_warn_but_never_block(monkeypatch):
    fake(monkeypatch, applied=("0039",), heads=("0039", "0039b"))
    assert dbcheck.status().state == "multiple_heads"
    assert dbcheck.check(get_settings()) == ""


def test_a_revision_the_code_does_not_know_is_reported(monkeypatch):
    fake(monkeypatch, applied=("9999",), heads=("0040",))
    assert dbcheck.status().state == "unknown_revision"


def test_hosted_refuses_to_start_when_behind(monkeypatch):
    fake(monkeypatch, applied=("0039",), heads=("0040",))
    monkeypatch.setattr(get_settings(), "deploy_mode", "hosted")
    with pytest.raises(dbcheck.DatabaseBehind, match="alembic upgrade head"):
        dbcheck.check(get_settings())


def test_not_hosted_logs_loudly_and_returns_the_message(monkeypatch, caplog):
    fake(monkeypatch, applied=("0039",), heads=("0040",))
    monkeypatch.setattr(get_settings(), "deploy_mode", "self_hosted")
    with caplog.at_level("ERROR"):
        message = dbcheck.check(get_settings())
    assert "behind the code" in message
    assert any("DATABASE NOT READY" in r.getMessage() for r in caplog.records)


def test_while_behind_every_route_but_health_says_so_instead_of_a_500(monkeypatch):
    app = create_app()
    client = TestClient(app)
    app.state.db_gate.message = "The database is behind the code. Run alembic upgrade head."
    monkeypatch.setattr(dbcheck, "RECHECK_EVERY_S", 10_000.0)
    r = client.get("/app/onboarding")
    assert r.status_code == 503
    assert "alembic upgrade head" in r.json()["detail"]
    h = client.get("/health")
    assert h.status_code == 200
    assert h.json()["status"] == "degraded" and h.json()["database"] == "behind"


def test_it_clears_by_itself_once_the_database_catches_up(monkeypatch):
    app = create_app()
    client = TestClient(app)
    app.state.db_gate.message = "The database is behind the code. Run alembic upgrade head."
    monkeypatch.setattr(dbcheck, "RECHECK_EVERY_S", 0.0)
    fake(monkeypatch, applied=("0040",), heads=("0040",))
    assert client.get("/health").json() == {"status": "ok"}
    assert app.state.db_gate.message == ""
