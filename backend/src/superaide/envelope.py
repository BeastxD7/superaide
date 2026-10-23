"""One response shape for the whole API (ADR 0013).

Every JSON response, success or error, is

    {"status": "success" | "error", "status_code": <HTTP status>, "message": "<a sentence, never empty>",
     "data": <payload or null>, "error": null | {"code": "...", "detail": ..., "request_id": "..."}}

This module is the single place that builds it. Phase 1 (here): errors, plus a request id on every response
(`X-Request-ID`, generated when the caller sends none) that also prefixes every log line made while the request runs.
For one release the error body keeps the old top-level `detail` too, so a client that has not moved yet still works.
"""

from __future__ import annotations

import contextvars
import http
import logging
import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_log_installed = False
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Stable machine codes, one per HTTP status. A route may override with {"code": ...} in its HTTPException detail.
CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    423: "locked",
    429: "rate_limited",
    501: "not_implemented",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}

DEFAULT_MESSAGES = {
    400: "That request wasn't valid.",
    401: "Please sign in to continue.",
    403: "You don't have access to that.",
    404: "That doesn't exist.",
    405: "That action isn't allowed here.",
    408: "The request took too long. Try again.",
    409: "That conflicts with what's already saved.",
    410: "That is no longer available.",
    413: "That is too large to accept.",
    415: "That kind of content isn't supported.",
    422: "Check the form and try again.",
    423: "That is locked right now. Try again later.",
    429: "Too many requests. Wait a moment and try again.",
    500: "Something went wrong on our side. Try again in a moment.",
    501: "That isn't available yet.",
    502: "A service Superaide depends on didn't answer. Try again in a moment.",
    503: "Superaide is not available right now. Try again in a moment.",
    504: "A service Superaide depends on took too long. Try again in a moment.",
}


def code_for(status: int) -> str:
    if status in CODES:
        return CODES[status]
    return "internal_error" if status >= 500 else "request_failed"


def default_message(status: int) -> str:
    if status in DEFAULT_MESSAGES:
        return DEFAULT_MESSAGES[status]
    return DEFAULT_MESSAGES[500] if status >= 500 else DEFAULT_MESSAGES[400]


def current_request_id() -> str:
    return _request_id.get()


def new_request_id(incoming: str | None) -> str:
    """Keep a caller's id when it is short and plain (so a proxy's id threads through), else make one."""
    if incoming and _SAFE_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


def error_body(
    status: int,
    message: str | None = None,
    *,
    code: str | None = None,
    detail: Any = None,
    legacy_detail: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """The error envelope. `legacy_detail` is the old top-level `detail` (kept for one release)."""
    body: dict[str, Any] = {
        "status": "error",
        "status_code": status,
        "message": (message or "").strip() or default_message(status),
        "data": None,
        "error": {
            "code": code or code_for(status),
            "detail": jsonable_encoder(detail),
            "request_id": request_id or current_request_id(),
        },
    }
    body["detail"] = jsonable_encoder(legacy_detail if legacy_detail is not None else body["message"])
    return body


def error_response(
    status: int,
    message: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    **kw: Any,
) -> JSONResponse:
    rid = kw.get("request_id") or current_request_id()
    out = {REQUEST_ID_HEADER: rid, **(headers or {})} if rid else dict(headers or {})
    return JSONResponse(error_body(status, message, **kw), status_code=status, headers=out)


def _from_http_exception(exc: StarletteHTTPException) -> JSONResponse:
    status = exc.status_code
    raw = exc.detail
    phrase = http.HTTPStatus(status).phrase if status in http.HTTPStatus._value2member_map_ else ""
    message: str | None = None
    code: str | None = None
    detail: Any = None
    if isinstance(raw, dict):
        message = raw.get("message") if isinstance(raw.get("message"), str) else None
        code = raw.get("code") if isinstance(raw.get("code"), str) else None
        detail = {k: v for k, v in raw.items() if k not in ("message", "code")} or None
    elif isinstance(raw, str):
        message = None if raw == phrase else raw  # Starlette's own "Not Found" becomes our sentence
    elif raw is not None:
        detail = raw
    return error_response(
        status,
        message,
        code=code,
        detail=detail,
        legacy_detail=raw if raw is not None else None,
        headers=dict(exc.headers or {}),
    )


def validation_message(errors: list[dict[str, Any]]) -> str:
    first = errors[0] if errors else {}
    msg = str(first.get("msg") or "").strip()
    return msg or DEFAULT_MESSAGES[422]


def install(app: FastAPI) -> None:
    """Register the handlers and the request-id middleware on `app`."""

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _from_http_exception(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's own 422, minus the "input" it echoes back: that can be a password or an API key
        (for a missing field it is the whole body, other credentials included)."""
        errors = [{k: v for k, v in error.items() if k != "input"} for error in exc.errors()]
        errors = jsonable_encoder(errors)
        return error_response(
            422, validation_message(errors), code="validation_failed", detail=errors, legacy_detail=errors
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never say what broke: the log has the traceback, the caller gets the request id to quote.
        rid = getattr(request.state, "request_id", "") or current_request_id() or new_request_id(None)
        log.error("unhandled error in %s %s", request.method, request.url.path, exc_info=exc)
        return error_response(500, None, code="internal_error", request_id=rid)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = new_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = rid
        token = _request_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


def install_log_context() -> None:
    """Prefix every log line made during a request with its id: `[req abc123] message`."""
    global _log_installed
    if _log_installed:
        return
    _log_installed = True
    make = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = make(*args, **kwargs)
        rid = _request_id.get()
        if rid:
            record.request_id = rid
            if isinstance(record.msg, str):
                record.msg = f"[req {rid}] {record.msg}"
        return record

    logging.setLogRecordFactory(factory)
