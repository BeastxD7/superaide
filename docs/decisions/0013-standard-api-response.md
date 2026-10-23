# 0013: One response shape for the whole API

Status: accepted 2026-10-23. Phase 1 (errors, request id) built; phase 2 (success envelope) follows in its own PR.

## Context

The owner's requirement: "we need a standard API response: status, message and error. Every API response follows one standard shape, even when it is an error."

Today every route answers in its own shape. Errors are FastAPI's `{"detail": "..."}`, where `detail` is sometimes a string, sometimes an object (`{"message", "field"}`), sometimes a list (validation). A 404 for a missing route reads `Not Found`, and the 503 for a database that is behind the code is a bare `{"detail": ...}`. Successes are bare payloads. A client has to guess which shape it got, and a support request has nothing to quote.

## Decision

Every JSON response uses one envelope:

```json
{
  "status": "success",
  "status_code": 200,
  "message": "OK",
  "data": { "...": "the payload" },
  "error": null
}
```

```json
{
  "status": "error",
  "status_code": 404,
  "message": "That doesn't exist.",
  "data": null,
  "error": {
    "code": "not_found",
    "detail": null,
    "request_id": "9f2c1c0e4b7a4d0e8d3c6a5b2f1e0d9c"
  }
}
```

- `status` is `success` or `error`. `status_code` repeats the HTTP status in the body, which is not lost when a proxy or a log keeps only the body. The HTTP status itself stays meaningful (404 stays 404, 422 stays 422).
- `message` is always one human sentence and never empty. Errors take the sentence the route already wrote (`HTTPException("Ava was just hired by someone else.")`); where there was none (a missing route, an unhandled error) a fixed default for that status is used. Success messages come from a default per verb and status, which an endpoint can override (phase 2).
- `error.code` is a stable machine code a client can switch on: `bad_request`, `unauthorized`, `forbidden`, `not_found`, `method_not_allowed`, `conflict`, `validation_failed` (422), `rate_limited`, `service_unavailable`, `internal_error`. One code per HTTP status unless a route names its own.
- `error.detail` is optional structured detail: the per-field list for validation, or the extra keys of a structured `HTTPException` (`{"message": ..., "needs_price": [...]}` gives `message` plus `detail: {"needs_price": [...]}`).
- `error.request_id` is the `X-Request-ID` of the request. The header is read from the caller when it is short and plain (so a proxy's id threads through), otherwise generated, and it is returned on every response. Every log line made while the request runs is prefixed `[req <id>]`, so a report that quotes the id finds the traceback.
- An unhandled exception is a 500 with code `internal_error`, the default sentence and the request id. Nothing about the exception reaches the caller; the traceback goes to the log only.
- Validation errors never echo the submitted `input` (it can be a password or a key); that rule from before is unchanged.

### One place

`backend/src/superaide/envelope.py` builds every envelope. It registers handlers for `HTTPException` (which also covers Starlette's 404 and 405), `RequestValidationError` and `Exception`, plus the request-id middleware. The "database behind the code" 503 in `main.py` calls the same builder. No route builds an error body by hand.

### Rollout

1. **Phase 1, errors.** All error responses use the envelope and carry `X-Request-ID`. For one release the old top-level `detail` key stays beside the envelope (same value as before), so a client that has not moved still works. The frontend reads `message` (then `error.detail` for the field), falling back to `detail`.
2. **Phase 2, success.** Every JSON success response is wrapped in one place (a response wrapper plus the OpenAPI schema generator). Exempt, because they cannot be wrapped: server-sent-event streams, file and zip downloads, redirects, third-party webhooks and OAuth callbacks that answer in the provider's format. The frontend HTTP client unwraps `data` in one place so feature code does not change. After the release, `detail` is dropped.

### Contract tests

A test sweeps every route in the OpenAPI table and fails if a 4xx or 5xx is not the envelope, has an empty `message`, lacks `status_code`, or lacks a matching request id. Phase 2 extends the sweep to successes, with the exemptions listed explicitly.

## Consequences

- Clients have one parser. A bug report quotes a request id.
- A second field `status_code` duplicates the HTTP status; accepted so the body is self-describing.
- Backward compatibility is a deliberate, temporary cost: `detail` is duplicated for one release.

## Alternatives

- RFC 9457 `application/problem+json` for errors only. Standard, but the owner asked for one shape for success and error alike, and a client then has two parsers.
- Wrapping only on a version bump (`/v2`). There is one first-party client and it ships with the server, so a flag day with a compatibility key is simpler.
