"""Sending a push to the owner's devices (ADR 0011 sections 3.3 and 3.5).

Encryption (RFC 8291, aes128gcm) and the VAPID signature (RFC 8292) come from the pinned
`pywebpush`/`py-vapid` libraries; the HTTP request is ours (httpx), so the TTL, Urgency and Topic
headers and the answers' handling are in one place:

- 201 (or any 2xx): sent. That only means the push service took it; nothing here counts as seen.
- 404 / 410: the subscription is gone; the row is disabled and marked `gone_at`.
- 413: a payload bug (the padded size is fixed); logged as an error, not retried.
- 429 / 5xx / network errors: retried with backoff, at most `push_max_attempts` times.

Each device gets its own body, built for its own preview level, padded to the same fixed size.
One `push_send` row per device and attempt run; for a ladder rung (`delivery_id`), a device
already reached is skipped on a retry.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from py_vapid import Vapid02
from pywebpush import WebPusher
from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import get_settings
from superaide.push import payload, vapid
from superaide.push.models import PushSend, PushSubscription
from superaide.push.subscriptions import open_secret

log = logging.getLogger(__name__)
VAPID_EXPIRY_S = 12 * 60 * 60


@dataclass(frozen=True)
class Outcome:
    subscription_id: uuid.UUID
    status: str  # sent | failed | gone | skipped
    http_status: int | None = None
    attempts: int = 0


def encrypt_body(subscription_info: dict[str, object], plaintext: bytes) -> bytes:
    body = WebPusher(subscription_info).encode(plaintext, content_encoding="aes128gcm")["body"]
    return bytes(body)


def vapid_header(private_pem: str, endpoint: str, subject: str, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now or time.time()) + VAPID_EXPIRY_S),
        "sub": subject,
    }
    return str(Vapid02.from_pem(private_pem.encode()).sign(claims)["Authorization"])


def eligible(row: PushSubscription, now: datetime) -> bool:
    return row.enabled and row.gone_at is None and (row.muted_until is None or row.muted_until <= now)


def _post(client: httpx.Client, endpoint: str, body: bytes, hdrs: dict[str, str]) -> int | None:
    try:
        return client.post(endpoint, content=body, headers=hdrs).status_code
    except httpx.HTTPError:
        return None  # network trouble: retried like a 5xx


def send(
    session: Session,
    item: payload.PushItem,
    subscriptions: Iterable[PushSubscription],
    *,
    purpose: str,
    instance: str,
    delivery_id: uuid.UUID | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Outcome]:
    """Push `item` to each eligible device. Returns what happened per device; never raises for a
    device's failure. Raises only if push is off (no VAPID keys) or the payload is a bug (too big)."""
    keys = vapid.load(session)
    if keys is None:
        raise RuntimeError("push is not set up: no VAPID keys")
    now = datetime.now(UTC)
    already: set[uuid.UUID] = set()
    if delivery_id is not None:
        already = set(
            session.scalars(
                select(PushSend.subscription_id).where(PushSend.delivery_id == delivery_id, PushSend.status == "sent")
            )
        )
    own_client = client is None
    http = client or httpx.Client(timeout=10.0, follow_redirects=False)
    outcomes: list[Outcome] = []
    try:
        for row in subscriptions:
            if not eligible(row, now) or row.id in already:
                outcomes.append(Outcome(row.id, "skipped"))
                continue
            outcomes.append(_send_one(session, http, row, item, keys, purpose, instance, delivery_id, sleep))
    finally:
        if own_client:
            http.close()
    counts = {s: sum(1 for o in outcomes if o.status == s) for s in ("sent", "failed", "gone", "skipped")}
    action = "push_sent" if counts["sent"] else "push_failed"
    audit(session, "push", action, purpose=purpose, kind=item.kind, delivery_id=delivery_id, **counts)
    return outcomes


def _send_one(
    session: Session,
    http: httpx.Client,
    row: PushSubscription,
    item: payload.PushItem,
    keys: vapid.VapidKeys,
    purpose: str,
    instance: str,
    delivery_id: uuid.UUID | None,
    sleep: Callable[[float], None],
) -> Outcome:
    settings = get_settings()
    secret = open_secret(row)
    body = payload.build(item, row.preview_level, base_url=settings.frontend_origin, instance=instance)
    plaintext = payload.encode(body, settings.push_pad_bytes)
    hdrs = {
        **payload.headers(item, settings.push_ttl_seconds),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": vapid_header(keys.private_pem, secret.endpoint, settings.vapid_subject),
    }
    attempts, code = 0, None
    max_attempts = max(1, settings.push_max_attempts)
    while attempts < max_attempts:
        attempts += 1
        # A fresh encryption per attempt: a new ephemeral key and salt each time.
        code = _post(http, secret.endpoint, encrypt_body(secret.as_info(), plaintext), hdrs)
        if code is not None and (code < 500 and code != 429):
            break
        if attempts < max_attempts:
            sleep(min(2 ** (attempts - 1), 8))
    now = datetime.now(UTC)
    if code is not None and 200 <= code < 300:
        status = "sent"
        row.last_success_at, row.failure_count = now, 0
    elif code in (404, 410):
        status = "gone"
        row.gone_at, row.enabled, row.last_failure_at = now, False, now
        audit(session, "push", "push_gone", subscription_id=row.id, http_status=code)
    else:
        status = "failed"
        row.last_failure_at, row.failure_count = now, row.failure_count + 1
        if code == 413:
            log.error("push payload refused as too large (%s bytes padded): a bug", settings.push_pad_bytes)
    session.add(
        PushSend(
            delivery_id=delivery_id, subscription_id=row.id, purpose=purpose, status=status, http_status=code,
            attempts=attempts, error="" if status == "sent" else f"http {code}" if code else "network",
        )
    )  # fmt: skip
    session.flush()
    return Outcome(row.id, status, code, attempts)
