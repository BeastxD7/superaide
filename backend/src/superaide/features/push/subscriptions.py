"""Saving and reading the owner's push subscriptions (ADR 0011 sections 3.1 and 3.6).

The endpoint URL and its keys are secrets: whoever holds them can push to the device. They are
stored only encrypted, with "{agent_id}/push/{id}" as associated data, so a row copied to another
instance (or another row) fails to decrypt. Lookups use the endpoint's SHA-256.

The server POSTs to whatever endpoint a browser hands it, so only https endpoints on the known
push services are accepted (no requests to internal hosts on a client's say-so).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.features.push.models import PushSubscription
from superaide.identity.vault.crypto import decrypt, encrypt
from superaide.platform.audit import audit

# The browsers' push services. A subscription to any other host is refused.
PUSH_HOST_SUFFIXES: tuple[str, ...] = (
    "push.apple.com",  # Safari, iOS/iPadOS Home Screen apps
    "fcm.googleapis.com",  # Chrome, Edge on Android, Brave, Opera
    "push.services.mozilla.com",  # Firefox
    "notify.windows.com",  # Edge on Windows
)


class BadSubscription(ValueError):
    """A plain-language reason a subscription can't be saved."""


@dataclass(frozen=True)
class Secret:
    endpoint: str
    p256dh: str
    auth: str

    def as_info(self) -> dict[str, object]:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def push_host(endpoint: str) -> str:
    """The endpoint's host, if it is an https push service we send to; otherwise BadSubscription."""
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.port not in (None, 443) or parts.username or parts.password:
        raise BadSubscription("That isn't a browser push address.")
    if not any(host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES):
        raise BadSubscription("That push service isn't supported.")
    return host


def _aad(agent_id: uuid.UUID, row_id: uuid.UUID) -> str:
    return f"{agent_id}/push/{row_id}"


def seal(secret: Secret, agent_id: uuid.UUID, row_id: uuid.UUID) -> str:
    body = json.dumps({"endpoint": secret.endpoint, "p256dh": secret.p256dh, "auth": secret.auth})
    return encrypt(body, _aad(agent_id, row_id))


def open_secret(row: PushSubscription) -> Secret:
    data = json.loads(decrypt(row.secret_enc, _aad(row.agent_id, row.id)))
    return Secret(endpoint=data["endpoint"], p256dh=data["p256dh"], auth=data["auth"])


def upsert(
    session: Session,
    *,
    secret: Secret,
    user_id: uuid.UUID,
    person_id: uuid.UUID,
    session_id: uuid.UUID | None,
    agent_id: uuid.UUID,
    platform: str,
    label: str,
    default_preview: str,
) -> tuple[PushSubscription, bool]:
    """Save this browser's subscription for the owner. Re-saving the same endpoint refreshes it
    (`last_seen_at`) and, if another account or session made it, moves it to this one. True if new."""
    host = push_host(secret.endpoint)
    digest = endpoint_hash(secret.endpoint)
    now = datetime.now(UTC)
    row = session.scalar(
        select(PushSubscription).where(PushSubscription.agent_id == agent_id, PushSubscription.endpoint_hash == digest)
    )
    created = row is None
    if row is None:
        row = PushSubscription(id=uuid.uuid4(), agent_id=agent_id, endpoint_hash=digest, preview_level=default_preview)
        session.add(row)
    elif row.user_id != user_id:
        row.preview_level = default_preview  # a new holder doesn't inherit the old one's choices
        row.muted_until = None
    row.user_id, row.person_id, row.session_id = user_id, person_id, session_id
    row.secret_enc = seal(secret, agent_id, row.id)
    row.push_host = host
    row.platform = platform
    if label or created:
        row.device_label = label
    row.enabled, row.gone_at, row.last_seen_at = True, None, now
    session.flush()
    if created:
        audit(session, "push", "push_subscribed", subscription_id=row.id, platform=platform, host=host)
    return row, created


def remove(session: Session, row: PushSubscription, *, why: str) -> None:
    audit(session, "push", "push_unsubscribed", subscription_id=row.id, why=why)
    session.delete(row)
