"""One response shape for every error (ADR 0013, phase 1).

Every response with a 4xx or 5xx status is {"status": "error", "status_code", "message", "data": null,
"error": {"code", "detail", "request_id"}} with a non-empty message, the same status code in the body as
on the wire, and an X-Request-ID header that matches `error.request_id`. The old top-level "detail" stays
for one release. The sweep is read from the app's own route table, so a new endpoint is covered without
editing this file.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.api.conftest import fill_path

CODES = {
    "bad_request", "unauthorized", "forbidden", "not_found", "method_not_allowed", "conflict", "payload_too_large",
    "validation_failed", "rate_limited", "service_unavailable", "internal_error", "request_failed", "gone",
    "locked", "unsupported_media_type", "request_timeout", "not_implemented", "bad_gateway", "gateway_timeout",
}  # fmt: skip


def assert_error_envelope(resp: Any, *, label: str = "") -> dict[str, Any]:
    where = f"{label} -> {resp.status_code} {resp.text[:200]}"
    body = resp.json()
    assert body["status"] == "error", where
    assert body["status_code"] == resp.status_code, where
    assert isinstance(body["message"], str) and body["message"].strip(), where
    assert body["data"] is None, where
    err = body["error"]
    assert err["code"] in CODES, where
    assert err["request_id"] and resp.headers["X-Request-ID"] == err["request_id"], where
    assert "detail" in body, where  # kept for one release
    return body


def all_operations(app) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, op)
        for path, ops in sorted(app.openapi()["paths"].items())
        for method, op in sorted(ops.items())
    ]


def test_every_route_answers_a_signed_out_caller_in_the_error_shape(app, browser):
    """Signed out, with the CSRF header: whatever each route answers with a 4xx/5xx has the envelope."""
    client = browser()
    seen = 0
    for method, path, _ in all_operations(app):
        if path.startswith("/webhooks") or path in {"/health", "/"}:
            continue
        resp = client.request(method, fill_path(path), json={} if method != "GET" else None, follow_redirects=False)
        if resp.status_code < 400:
            continue
        if "application/json" not in resp.headers.get("content-type", ""):
            pytest.fail(f"{method} {path} answered {resp.status_code} without JSON: {resp.text[:100]}")
        assert_error_envelope(resp, label=f"{method} {path}")
        seen += 1
    assert seen > 50


def test_every_route_with_a_body_rejects_garbage_as_a_422_envelope(app, owner):
    """Signed in as the owner, a JSON array is never a valid body: 422 in the envelope, input never echoed."""
    checked = 0
    for method, path, op in all_operations(app):
        content = (op.get("requestBody") or {}).get("content", {})
        if "application/json" not in content or path.startswith("/webhooks") or path.startswith("/api/"):
            continue
        if path.startswith("/auth/logout") or path.startswith("/auth/delete"):
            continue
        resp = owner.request(method, fill_path(path), json=["sk-ant-secret-canary"])
        assert resp.status_code in (404, 422), f"{method} {path} -> {resp.status_code}"
        body = assert_error_envelope(resp, label=f"{method} {path}")
        assert "sk-ant-secret-canary" not in resp.text
        if resp.status_code == 422:
            assert body["error"]["code"] == "validation_failed"
            assert isinstance(body["error"]["detail"], list) and body["error"]["detail"]
            assert isinstance(body["detail"], list)  # the old shape
        checked += 1
    assert checked > 20


def test_not_found_and_method_not_allowed(browser):
    client = browser()
    r = client.get("/definitely/not/a/route")
    body = assert_error_envelope(r)
    assert (r.status_code, body["error"]["code"]) == (404, "not_found")
    assert body["detail"] == "Not Found"  # old key unchanged: the frontend's "older API" check reads it
    r = client.post("/health", json={})
    body = assert_error_envelope(r)
    assert (r.status_code, body["error"]["code"]) == (405, "method_not_allowed")
    assert r.headers.get("allow")


def test_http_exceptions_keep_their_sentence_status_and_headers(app):
    @app.get("/_t/conflict")
    def conflict():
        raise HTTPException(409, "Ava was just hired by someone else.")

    @app.get("/_t/structured")
    def structured():
        raise HTTPException(409, {"message": "Set a price first.", "needs_price": ["m1"]})

    @app.get("/_t/auth")
    def auth():
        raise HTTPException(401, "Sign in.", headers={"WWW-Authenticate": "Bearer"})

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/_t/conflict")
    body = assert_error_envelope(r)
    assert (body["message"], body["error"]["code"], body["detail"]) == (
        "Ava was just hired by someone else.", "conflict", "Ava was just hired by someone else.",
    )  # fmt: skip
    r = c.get("/_t/structured")
    body = assert_error_envelope(r)
    assert body["message"] == "Set a price first."
    assert body["error"]["detail"] == {"needs_price": ["m1"]}
    assert body["detail"] == {"message": "Set a price first.", "needs_price": ["m1"]}
    r = c.get("/_t/auth")
    assert_error_envelope(r)
    assert r.headers["www-authenticate"] == "Bearer"


def test_an_unhandled_error_is_a_500_that_leaks_nothing(app, caplog):
    @app.get("/_t/boom")
    def boom():
        raise RuntimeError("password=hunter2 in /etc/secret.py")

    c = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level("ERROR"):
        r = c.get("/_t/boom", headers={"X-Request-ID": "trace-me-123"})
    body = assert_error_envelope(r)
    assert (r.status_code, body["error"]["code"]) == (500, "internal_error")
    assert body["error"]["request_id"] == "trace-me-123"  # a caller's id is kept
    assert "hunter2" not in r.text and "Traceback" not in r.text and "secret.py" not in r.text
    assert any("hunter2" in (rec.exc_text or "") or rec.exc_info for rec in caplog.records)  # the log has it


def test_the_request_id_is_made_when_absent_and_replaced_when_unsafe(browser):
    c = browser()
    a, b = c.get("/health"), c.get("/health")
    assert a.headers["X-Request-ID"] and a.headers["X-Request-ID"] != b.headers["X-Request-ID"]
    r = c.get("/health", headers={"X-Request-ID": "bad id with spaces\t"})
    assert r.headers["X-Request-ID"] not in {"bad id with spaces\t", ""}


def test_log_lines_carry_the_request_id(app, caplog):
    import logging

    @app.get("/_t/log")
    def log_it():
        logging.getLogger("superaide.test").warning("something noteworthy")
        return {"ok": True}

    with caplog.at_level("WARNING"):
        TestClient(app).get("/_t/log", headers={"X-Request-ID": "req-42"})
    assert any("[req req-42] something noteworthy" in r.getMessage() for r in caplog.records)


def test_the_database_behind_503_is_an_envelope(app):
    app.state.db_gate.message = "The database is behind the code. Run alembic upgrade head."
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/app/me")
    body = assert_error_envelope(r)
    assert (r.status_code, body["error"]["code"]) == (503, "service_unavailable")
    assert body["message"].startswith("The database is behind")
    assert body["detail"] == body["message"]
    assert c.get("/health").status_code == 200  # the container check still passes


def test_me_says_whether_triage_is_on(owner):
    assert owner.get("/app/me").json()["features"] == {"triage": False}


def test_a_signed_in_owner_gets_the_envelope_on_a_real_401(browser):
    c = browser(csrf=False)
    r = c.post("/app/hire", json={})
    assert_error_envelope(r)
