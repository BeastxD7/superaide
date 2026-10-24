"""The deployment's VAPID key pair (ADR 0011 section 3.2).

Every subscription on this origin is made with one `applicationServerKey`, so there is one pair
per deployment, not per instance. It is a platform credential (ADR 0009 section 6): it lives in
`platform_state` (no agent_id, outside Row-Level Security), never in an instance's vault, so every
instance reads the same pair. The private key is encrypted with AES-256-GCM under the master key
with the platform slot "platform/vapid" as associated data; the public key isn't secret (every
browser gets it) and is stored as is. Only this module reads the row.

Keys are generated only when PUSH_ENABLED is on and none exist. Rotating the pair invalidates
every subscription, so nothing here rotates; `superaide push-keys` only creates a missing pair.
Neither key is ever logged; only the public key is ever returned.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.session import rows_affected
from superaide.platform.tenancy.models import PlatformState
from superaide.vault.crypto import decrypt, encrypt

KEY = "vapid"  # the platform_state row
SLOT = "platform/vapid"  # associated data for the private key: it decrypts nowhere else


@dataclass(frozen=True)
class VapidKeys:
    private_pem: str  # PKCS#8 PEM, for signing the VAPID JWT
    public_key: str  # base64url of the uncompressed P-256 point: the browser's applicationServerKey


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def load(session: Session) -> VapidKeys | None:
    """The stored pair, or None if there is none yet."""
    row = session.get(PlatformState, KEY)
    if row is None or not row.value.get("private_enc") or not row.value.get("public_key"):
        return None
    return VapidKeys(private_pem=decrypt(row.value["private_enc"], SLOT), public_key=row.value["public_key"])


def generate() -> VapidKeys:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    point = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return VapidKeys(private_pem=pem, public_key=_b64url(point))


def ensure(session: Session) -> VapidKeys | None:
    """The pair, made once if PUSH_ENABLED is on and none exists. None while push is off.
    Two first uses at the same moment keep whichever pair was stored first."""
    found = load(session)
    if found is not None or not get_settings().push_enabled:
        return found
    keys = generate()
    value = {"private_enc": encrypt(keys.private_pem, SLOT), "public_key": keys.public_key}
    stored = session.execute(
        insert(PlatformState).values(key=KEY, value=value).on_conflict_do_nothing(index_elements=["key"])
    )
    if rows_affected(stored):
        audit(session, "push", "vapid_generated")  # never the keys
    session.expire_all()
    return load(session)
