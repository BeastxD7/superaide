"""Keyset (cursor) pagination: the one way a list endpoint returns a slice of a big table (ADR 0014).

Why keyset and not OFFSET: `OFFSET n` reads and throws away n rows, so page 10 000 costs as much as
10 000 pages, and a row inserted while someone pages shifts every later page (a duplicate or a skip).
A keyset page asks "the 25 rows after (created_at, id) = X", which an index on those columns answers
by seeking straight to X. Cost is flat at any depth and insertions never disturb the pages already read.

What an endpoint does:

    class Item(BaseModel): ...

    @router.get("/things", response_model=Page[Item])
    def things(limit: int = limit_param(25, 100), cursor: str | None = cursor_param(),
               owner: uuid.UUID = Depends(owner_person_id)) -> Page[Item]:
        with session_scope() as s:
            stmt = select(Thing).where(Thing.owner_person_id == owner)   # audience and tenant filters FIRST
            return paginate(
                s, stmt, [SortKey(Thing.created_at), SortKey(Thing.id)],
                endpoint="things", bind=bind_for(owner), limit=limit, cursor=cursor, build=item_of,
            )

Rules this module enforces so a call site cannot get them wrong:

* Every filter (tenant, owner, audience) is part of `stmt` before the cursor is applied; the cursor only ever
  narrows what the caller was already allowed to see.
* The sort always ends in a unique column (`id`), so rows with the same timestamp have a total order.
* The cursor is opaque, signed (HMAC-SHA256 under a key derived from the vault master key) and bound to the
  endpoint, the agent instance and (optionally) the person it was issued for, plus the sort it was issued
  for. A cursor copied to another endpoint, another tenant, another owner or a changed `order`
  is refused, and so is one that was edited or has expired.
* A bad cursor or limit is a 422 in the standard error envelope (ADR 0013), code `invalid_cursor`.
* Sort columns must be NOT NULL (keyset comparison with NULL is undefined); use a coalesced expression
  or another column if the real one is nullable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, literal, or_, text, tuple_
from sqlalchemy.orm import InstrumentedAttribute, Session

from superaide.platform.config import get_settings
from superaide.platform.tenancy.context import scope_or_default
from superaide.vault.crypto import master_key

DEFAULT_LIMIT = 25
HARD_MAX_LIMIT = 200  # no endpoint may allow more than this in one page
VERSION = "v1"
MAX_CURSOR_CHARS = 1024


class Page[T](BaseModel):
    """What every paginated endpoint returns. Under the ADR 0013 envelope this object is `data`."""

    items: list[T]
    next_cursor: str | None = Field(None, description="Pass as `cursor` to get the next page; null on the last page.")
    has_more: bool = Field(False, description="True when `next_cursor` leads to at least one more row.")
    total_estimate: int | None = Field(
        None, description="Approximate row count, only where it is cheap to know. Never an exact COUNT(*)."
    )


class InvalidCursor(HTTPException):
    def __init__(self, why: str) -> None:
        super().__init__(
            status_code=422,
            detail={
                "message": "That page link is not valid any more. Reload the list.",
                "code": "invalid_cursor",
                "why": why,
            },
        )


class InvalidLimit(HTTPException):
    def __init__(self, maximum: int) -> None:
        super().__init__(
            status_code=422,
            detail={"message": f"limit must be between 1 and {maximum}.", "code": "invalid_limit", "maximum": maximum},
        )


def limit_param(default: int = DEFAULT_LIMIT, maximum: int = 100) -> Any:
    """The `limit` query parameter, documented in OpenAPI and bounded (FastAPI answers 422 outside 1..maximum)."""
    maximum = min(maximum, HARD_MAX_LIMIT)
    return Query(default, ge=1, le=maximum, description=f"Page size, 1 to {maximum}. Default {default}.")


def cursor_param() -> Any:
    return Query(None, max_length=MAX_CURSOR_CHARS, description="Opaque cursor from the previous page's `next_cursor`.")


@dataclass(frozen=True)
class SortKey:
    """One part of the fixed sort order. `desc=True` is newest-first for a timestamp."""

    column: InstrumentedAttribute[Any]
    desc: bool = True

    @property
    def name(self) -> str:
        return str(self.column.key)


# --- signing ---------------------------------------------------------------------------------------


def _key() -> bytes:
    root, _ = master_key()
    return hmac.new(root, b"superaide pagination cursor signing key v1", hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text_: str) -> bytes:
    return base64.urlsafe_b64decode(text_ + "=" * (-len(text_) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def bind_for(subject: uuid.UUID | str | None = None) -> str:
    """What a cursor is tied to: the agent instance in scope, plus the person it was issued for."""
    return f"{scope_or_default().agent_id}:{subject or ''}"


def _order_sig(keys: Sequence[SortKey]) -> str:
    return ",".join(f"{k.name}:{'d' if k.desc else 'a'}" for k in keys)


def _enc(value: Any) -> list[Any]:
    if isinstance(value, bool):  # before int: bool is an int
        return ["b", value]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("sort keys must be timezone-aware datetimes")
        return ["t", value.isoformat()]
    if isinstance(value, uuid.UUID):
        return ["u", str(value)]
    if isinstance(value, int):
        return ["i", value]
    if isinstance(value, float):
        return ["f", value]
    if isinstance(value, str):
        return ["s", value]
    raise ValueError(f"cannot page by a {type(value).__name__}")


def _dec(pair: Any) -> Any:
    try:
        kind, value = pair
        if kind == "t":
            return datetime.fromisoformat(value)
        if kind == "u":
            return uuid.UUID(value)
        if kind == "i" and isinstance(value, int) and not isinstance(value, bool):
            return value
        if kind == "f" and isinstance(value, int | float):
            return float(value)
        if kind == "s" and isinstance(value, str):
            return value
        if kind == "b" and isinstance(value, bool):
            return value
    except TypeError, ValueError:
        pass
    raise InvalidCursor("bad value")


def encode_cursor(values: Sequence[Any], keys: Sequence[SortKey], *, endpoint: str, bind: str) -> str:
    """A signed cursor naming the last row of a page (its sort-key values, in sort order)."""
    if any(v is None for v in values):
        raise ValueError("a sort key is NULL; keyset pagination needs NOT NULL sort columns")
    payload = {
        "e": endpoint,
        "b": bind,
        "o": _order_sig(keys),
        "k": [_enc(v) for v in values],
        "x": int(time.time()) + get_settings().pagination_cursor_max_age_s,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{VERSION}.{body}.{_sign(f'{VERSION}.{body}')}"


def decode_cursor(cursor: str, keys: Sequence[SortKey], *, endpoint: str, bind: str) -> list[Any]:
    """The last row's sort-key values, or InvalidCursor (422). Checks signature, expiry, endpoint,
    instance/owner binding and sort order, in that order, in constant time for the signature."""
    if len(cursor) > MAX_CURSOR_CHARS:
        raise InvalidCursor("too long")
    parts = cursor.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise InvalidCursor("malformed")
    _, body, sig = parts
    if not hmac.compare_digest(sig, _sign(f"{VERSION}.{body}")):
        raise InvalidCursor("signature")
    try:
        payload = json.loads(_unb64(body))
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError as e:
        raise InvalidCursor("malformed") from e
    if payload.get("x", 0) < time.time():
        raise InvalidCursor("expired")
    if payload.get("e") != endpoint or payload.get("b") != bind:
        raise InvalidCursor("wrong list")
    if payload.get("o") != _order_sig(keys):
        raise InvalidCursor("different order")
    raw = payload.get("k")
    if not isinstance(raw, list) or len(raw) != len(keys):
        raise InvalidCursor("malformed")
    return [_dec(p) for p in raw]


# --- the SQL ---------------------------------------------------------------------------------------


def after_clause(keys: Sequence[SortKey], values: Sequence[Any]) -> Any:
    """`(k1, k2, ...) is after (v1, v2, ...)` in the sort order.

    All keys in one direction compile to a row comparison, `(created_at, id) < (:t, :id)`, which Postgres
    answers from a btree index on those columns directly. Mixed directions expand to the equivalent
    `k1 < v1 OR (k1 = v1 AND k2 > v2)` form."""
    vals = [literal(v, k.column.type) for k, v in zip(keys, values, strict=True)]
    if all(k.desc for k in keys):
        return tuple_(*[k.column for k in keys]) < tuple_(*vals)
    if not any(k.desc for k in keys):
        return tuple_(*[k.column for k in keys]) > tuple_(*vals)
    branches = []
    for i, k in enumerate(keys):
        eq = [keys[j].column == vals[j] for j in range(i)]
        cmp = k.column < vals[i] if k.desc else k.column > vals[i]
        branches.append(and_(*eq, cmp))
    return or_(*branches)


def page_query(
    stmt: Select[Any],
    keys: Sequence[SortKey],
    *,
    endpoint: str,
    bind: str,
    limit: int,
    cursor: str | None,
    maximum: int = HARD_MAX_LIMIT,
) -> Select[Any]:
    """`stmt` + the cursor condition + the fixed ORDER BY + `LIMIT limit + 1`. The exact query a page runs,
    exposed so tests can EXPLAIN it."""
    if not 1 <= limit <= min(maximum, HARD_MAX_LIMIT):
        raise InvalidLimit(min(maximum, HARD_MAX_LIMIT))
    if not keys:
        raise ValueError("at least one sort key (the unique id) is required")
    q = stmt
    if cursor:
        q = q.where(after_clause(keys, decode_cursor(cursor, keys, endpoint=endpoint, bind=bind)))
    return q.order_by(*[k.column.desc() if k.desc else k.column.asc() for k in keys]).limit(limit + 1)


def paginate[T](
    session: Session,
    stmt: Select[Any],
    keys: Sequence[SortKey],
    *,
    endpoint: str,
    bind: str,
    limit: int,
    cursor: str | None,
    build: Callable[[Any], T],
    entity_of: Callable[[Any], Any] = lambda row: row,
    maximum: int = HARD_MAX_LIMIT,
    total_estimate: int | None = None,
) -> Page[T]:
    """One page of `stmt` in the fixed order `keys` (the last must be unique, normally `id`).

    `stmt` already carries every tenant/owner/audience filter. We add the cursor condition and the ORDER BY,
    read `limit + 1` rows (the extra one only says whether there is more), and sign a cursor from the last
    row we return. `entity_of` picks the ORM object out of a row when `stmt` selects more than one thing."""
    q = page_query(stmt, keys, endpoint=endpoint, bind=bind, limit=limit, cursor=cursor, maximum=maximum)
    rows = list(session.execute(q).all() if _is_multi(stmt) else session.scalars(q).all())
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more and rows:
        last = entity_of(rows[-1])
        next_cursor = encode_cursor([getattr(last, k.name) for k in keys], keys, endpoint=endpoint, bind=bind)
    return Page[T](
        items=[build(r) for r in rows], next_cursor=next_cursor, has_more=more, total_estimate=total_estimate
    )


def _is_multi(stmt: Select[Any]) -> bool:
    """True when the statement selects more than one column/entity (rows are tuples, not single objects)."""
    return len(stmt.column_descriptions) > 1


# --- extras ----------------------------------------------------------------------------------------


def approx_count(session: Session, table: str) -> int | None:
    """Postgres' own planner estimate of a whole table's rows (no scan). It is table-wide, across tenants, and
    can lag by a vacuum, so use it only on operator endpoints and label it an estimate. None when unknown."""
    n = session.execute(
        text("SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(:t)"), {"t": table}
    ).scalar()
    return int(n) if n is not None and n >= 0 else None


def next_link(url: str, next_cursor: str | None) -> str | None:
    """A `Link: <url?cursor=...>; rel="next"` value, for clients that follow headers instead of bodies."""
    if not next_cursor:
        return None
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "cursor"] + [("cursor", next_cursor)]
    return f'<{urlunsplit(parts._replace(query=urlencode(query)))}>; rel="next"'


def set_next_headers(response: Response, url: str, next_cursor: str | None) -> None:
    """Additive compatibility for endpoints that still answer a bare JSON array: the next page rides in
    headers (`X-Next-Cursor`, `Link`) so the body shape does not change for existing clients."""
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
        response.headers["Link"] = next_link(url, next_cursor) or ""
