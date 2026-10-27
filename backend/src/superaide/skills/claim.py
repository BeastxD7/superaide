"""A short-lived, bucketed trust claim that Ava's Agent Card can carry (issue #131, phase two).

"The owner approves about 90% of my sends unedited." The claim is built only from the receipt log, in code:

- the rate is rounded to the nearest 10% and the count only to "10+" or "30+", never exact;
- it names an effect class, never a contact, a message or a skill argument;
- it expires in 24 hours and carries a reference that is different for every counterparty, so two agents can't
  compare notes to track one Ava;
- it is signed (HS256, a compact token like a JWS) with a key derived from the vault master key. Anyone holding the
  receipt log can check it with `check_against_log`.

Nothing here talks to another agent or moves money. Off unless SKILL_RECEIPTS_ENABLED.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from sqlalchemy.orm import Session

from superaide.skills import receipts

TTL_SECONDS = 24 * 3600
MIN_SAMPLE = 10
EFFECT = "send"


def _key() -> bytes:
    from superaide.identity.vault.crypto import master_key

    root, _ = master_key()
    return hmac.new(root, b"skill trust claim signing key v1", hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def bucket_rate(rate: float) -> int:
    return min(100, max(0, round(rate * 10) * 10))


def current_rate(rows: list[dict[str, Any]], effect: str = EFFECT) -> tuple[float, int] | None:
    """(share of decided actions approved unedited, how many) for one effect class, or None below the minimum."""
    decided = [r for r in rows if r["effect_class"] == effect and r["outcome"] in ("approved_unedited", "edited")]
    decided += [r for r in rows if r["effect_class"] == effect and r["outcome"] == "rejected"]
    if len(decided) < MIN_SAMPLE:
        return None
    return sum(r["outcome"] == "approved_unedited" for r in decided) / len(decided), len(decided)


def issue(session: Session, counterparty: str, *, now: float | None = None) -> str | None:
    """A signed claim for this counterparty, or None while there is too little in the log to say anything."""
    if not receipts.enabled():
        return None
    found = current_rate(receipts.rows(session))
    if found is None:
        return None
    rate, n = found
    issued = int(now if now is not None else time.time())
    ref = hmac.new(_key(), f"counterparty|{counterparty}|{issued // TTL_SECONDS}".encode(), hashlib.sha256)
    payload = {
        "claim": f"The owner approves about {bucket_rate(rate)}% of my sends unedited",
        "effect_class": EFFECT, "approved_unedited_pct": bucket_rate(rate), "sample": "30+" if n >= 30 else "10+",
        "ref": ref.hexdigest()[:16], "iat": issued, "exp": issued + TTL_SECONDS,
    }  # fmt: skip
    head = _b64(json.dumps({"alg": "HS256", "typ": "superaide-claim"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_key(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def verify(token: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The claim's payload if the signature is ours and it hasn't expired, else None."""
    try:
        head, body, sig = token.split(".")
        want = _b64(hmac.new(_key(), f"{head}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        payload: dict[str, Any] = json.loads(_unb64(body))
    except ValueError, TypeError:
        return None
    if (now if now is not None else time.time()) >= payload.get("exp", 0):
        return None
    return payload


def check_against_log(session: Session, token: str, *, now: float | None = None) -> bool:
    """True if the claim is valid, unexpired AND the log still shows a rate in the same 10% bucket (within one)."""
    payload = verify(token, now=now)
    found = current_rate(receipts.rows(session)) if payload else None
    return bool(payload and found and abs(bucket_rate(found[0]) - payload["approved_unedited_pct"]) <= 10)
