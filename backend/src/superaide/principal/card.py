"""Principal Card (issue #135): a signed, checkable statement that an outbound message comes from an AI.

The card is a small JSON (owner-chosen display name, what Ava can and cannot agree to, expiry) signed with a
key derived from the vault master key. The key is derived here and never returned, logged or stored. The
public page verifies the signature and expiry and shows only the card: it reads no memory, no contacts and no
relationships. The owner revokes every card at once by bumping a generation number kept in the instance's
own state. Off unless PRINCIPAL_CARD_ENABLED.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from superaide.config import get_settings
from superaide.policy.killswitch import get_state, set_state
from superaide.tenancy.context import InstanceScope, scope_or_default
from superaide.vault.crypto import master_key

STATE_KEY = "principal_card"
DEFAULT_CAN = ["Propose times", "Pass on messages"]
DEFAULT_CANNOT = ["Agree prices", "Sign anything", "Pay anyone"]
MAX_LISTED, MAX_ITEM, MAX_NAME = 8, 80, 80


def enabled() -> bool:
    return get_settings().principal_card_enabled


def _key() -> bytes:
    """HKDF-style (HMAC-SHA256) derivation, so the master key itself is never used as a signing key."""
    root, _ = master_key()
    return hmac.new(root, b"superaide principal card signing key v1", hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def settings_of(session: Session) -> dict[str, Any]:
    s = get_state(session, STATE_KEY)
    return {
        "display_name": str(s.get("display_name") or ""),
        "can": list(s.get("can") or DEFAULT_CAN),
        "cannot": list(s.get("cannot") or DEFAULT_CANNOT),
        "generation": int(s.get("generation") or 0),
    }


def _clean(items: list[str]) -> list[str]:
    return [" ".join(i.split())[:MAX_ITEM] for i in items if i.strip()][:MAX_LISTED]


def configure(
    session: Session, *, display_name: str, can: list[str] | None, cannot: list[str] | None
) -> dict[str, Any]:
    cur = settings_of(session)
    cur["display_name"] = " ".join(display_name.split())[:MAX_NAME]
    if can is not None:
        cur["can"] = _clean(can) or DEFAULT_CAN
    if cannot is not None:
        cur["cannot"] = _clean(cannot) or DEFAULT_CANNOT
    set_state(session, STATE_KEY, cur)
    return cur


def revoke_all(session: Session) -> int:
    cur = settings_of(session)
    cur["generation"] += 1
    set_state(session, STATE_KEY, cur)
    return int(cur["generation"])


def issue(session: Session, *, now: float | None = None) -> str | None:
    """A signed token for the instance in scope, or None when off or the owner hasn't set a display name."""
    if not enabled():
        return None
    cur = settings_of(session)
    if not cur["display_name"]:
        return None
    scope = scope_or_default()
    t = int(time.time() if now is None else now)
    payload = {
        "v": 1, "t": str(scope.tenant_id), "a": str(scope.agent_id), "g": cur["generation"],
        "n": cur["display_name"], "can": cur["can"], "cannot": cur["cannot"],
        "iat": t, "exp": t + get_settings().principal_card_days * 86400, "id": secrets.token_hex(6),
    }  # fmt: skip
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{body}.{_sign(body)}"


def scope_of(token: str) -> InstanceScope | None:
    """The instance named in a token, only if the signature holds (so it can be trusted to look up state)."""
    claims = _claims(token)
    if claims is None:
        return None
    try:
        return InstanceScope(tenant_id=uuid.UUID(claims["t"]), agent_id=uuid.UUID(claims["a"]))
    except KeyError, ValueError:
        return None


def _claims(token: str) -> dict[str, Any] | None:
    if len(token) > 4000 or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        claims = json.loads(_unb64(body))
    except ValueError:
        return None
    return claims if isinstance(claims, dict) and claims.get("v") == 1 else None


def verify(session: Session, token: str, *, now: float | None = None) -> dict[str, Any] | None:
    """What the public page shows, or None for a forged, expired or revoked card. The session must be in the
    token's own instance scope (see `scope_of`)."""
    if not enabled():
        return None
    claims = _claims(token)
    if claims is None or float(claims.get("exp", 0)) < (time.time() if now is None else now):
        return None
    if int(claims.get("g", -1)) != settings_of(session)["generation"]:
        return None
    return {
        "ai": True,
        "agent": get_settings().agent_name,
        "acts_for": str(claims["n"]),
        "can": [str(x) for x in claims.get("can", [])],
        "cannot": [str(x) for x in claims.get("cannot", [])],
        "valid_until": int(claims["exp"]),
        "notice": (
            f"This message was written by {get_settings().agent_name}, an AI assistant, acting for {claims['n']}."
        ),
    }


def footer(session: Session) -> str:
    """The line added under an outbound message, or empty."""
    token = issue(session)
    if token is None:
        return ""
    name = settings_of(session)["display_name"]
    base = get_settings().frontend_origin.rstrip("/")
    return (
        f"\n\n--\n{get_settings().agent_name} is an AI assistant acting for {name}. "
        f"What she may and may not agree to, and how to report a problem: {base}/c/{token}"
    )
