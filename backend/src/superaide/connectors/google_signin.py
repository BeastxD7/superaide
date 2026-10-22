"""Sign in with Google: OpenID Connect, Authorization Code with PKCE, scopes `openid email profile`.

Only who the person is comes from Google: the ID token's `sub` (Google's stable account id),
the email and whether Google verified it, and a display name. Calendar access is asked for
later, separately and only when the owner connects it (connectors/google.py).

The ID token is checked here in full, with no third-party JWT library: RS256 only, a key
from Google's published JWKS (cached, refreshed once on an unknown key id), issuer, audience
(our client id), expiry and the nonce we sent. Anything else is refused.

The URLs are module constants. A test or the E2E stack's fake Google may point them
elsewhere through `endpoints()`, which only honours an override when the simulator is on.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
SCOPES = ["openid", "email", "profile"]
JWKS_TTL = 3600.0  # seconds; Google rotates keys roughly weekly and publishes the next one early
CLOCK_SKEW = 120  # seconds of clock difference tolerated on exp/iat


class SignInError(Exception):
    """Google didn't vouch for this sign-in. `reason` is a short code for the redirect and the audit log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Endpoints:
    auth: str = AUTH_URL
    token: str = TOKEN_URL
    jwks: str = JWKS_URL


def endpoints() -> Endpoints:
    """Google's real URLs, or the E2E fake's when ENABLE_SIMULATOR is on and GOOGLE_FAKE_BASE_URL is set."""
    from superaide.config import get_settings

    settings = get_settings()
    base = settings.google_fake_base_url.rstrip("/")
    if base and settings.enable_simulator:
        return Endpoints(auth=f"{base}/o/oauth2/v2/auth", token=f"{base}/token", jwks=f"{base}/oauth2/v3/certs")
    return Endpoints()


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    return verifier, _b64url(hashlib.sha256(verifier.encode()).digest())


def nonce_for(secret: str) -> str:
    """What goes in the auth request (and comes back in the ID token): a hash, so the stored value
    alone can't be replayed as a nonce."""
    return _b64url(hashlib.sha256(f"superaide-nonce:{secret}".encode()).digest())


def auth_url(client_id: str, redirect_uri: str, *, state: str, challenge: str, nonce: str, base: str = "") -> str:
    return (
        (base or endpoints().auth)
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",  # someone with two Google accounts picks one
            }
        )
    )


def exchange_code(
    *, client_id: str, client_secret: str, code: str, verifier: str, redirect_uri: str, client: httpx.Client
) -> str:
    """Swap the code for tokens and return the ID token. The access token is not kept."""
    try:
        r = client.post(
            endpoints().token,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SignInError("unreachable") from exc
    if r.status_code != 200 or not isinstance(data, dict) or not isinstance(data.get("id_token"), str):
        raise SignInError("exchange_failed")
    return str(data["id_token"])


# --- the JWKS, cached -------------------------------------------------------------------


@dataclass
class _Keys:
    keys: dict[str, rsa.RSAPublicKey]
    fetched: float


_jwks: dict[str, _Keys] = {}
_jwks_lock = threading.Lock()


def clear_jwks_cache() -> None:
    with _jwks_lock:
        _jwks.clear()


def _rsa_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey | None:
    if jwk.get("kty") != "RSA" or jwk.get("use", "sig") != "sig" or jwk.get("alg", "RS256") != "RS256":
        return None
    try:
        n = int.from_bytes(_unb64url(str(jwk["n"])), "big")
        e = int.from_bytes(_unb64url(str(jwk["e"])), "big")
        return rsa.RSAPublicNumbers(e, n).public_key()
    except KeyError, ValueError, TypeError:
        return None


def _fetch_keys(url: str, client: httpx.Client) -> dict[str, rsa.RSAPublicKey]:
    try:
        r = client.get(url)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SignInError("unreachable") from exc
    if r.status_code != 200 or not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        raise SignInError("unreachable")
    keys = {}
    for jwk in data["keys"]:
        if isinstance(jwk, dict) and isinstance(jwk.get("kid"), str) and (key := _rsa_key(jwk)) is not None:
            keys[jwk["kid"]] = key
    return keys


def signing_key(kid: str, client: httpx.Client) -> rsa.RSAPublicKey:
    """Google's public key with this id. Cached for an hour; an unknown id refetches once (rotation)."""
    url = endpoints().jwks
    with _jwks_lock:
        cached = _jwks.get(url)
    fresh = cached is not None and time.time() - cached.fetched < JWKS_TTL
    if fresh and cached is not None and kid in cached.keys:
        return cached.keys[kid]
    keys = _fetch_keys(url, client)
    with _jwks_lock:
        _jwks[url] = _Keys(keys, time.time())
    if kid not in keys:
        raise SignInError("unknown_key")
    return keys[kid]


# --- the ID token ----------------------------------------------------------------------------


def verify_id_token(token: str, *, client_id: str, nonce: str, client: httpx.Client) -> GoogleIdentity:
    """Every check an OpenID Connect relying party must make, in order. Raises SignInError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise SignInError("malformed")
    try:
        header = json.loads(_unb64url(parts[0]))
        claims = json.loads(_unb64url(parts[1]))
        signature = _unb64url(parts[2])
    except ValueError as exc:
        raise SignInError("malformed") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise SignInError("malformed")
    # RS256 only: "none", HS256 (with the public key as the secret) and friends are refused outright.
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise SignInError("bad_algorithm")
    key = signing_key(header["kid"], client)
    try:
        key.verify(signature, f"{parts[0]}.{parts[1]}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise SignInError("bad_signature") from exc

    if claims.get("iss") not in ISSUERS:
        raise SignInError("bad_issuer")
    aud = claims.get("aud")
    if aud != client_id and not (isinstance(aud, list) and aud == [client_id]):
        raise SignInError("bad_audience")
    if "azp" in claims and claims["azp"] != client_id:
        raise SignInError("bad_audience")
    now = time.time()
    exp, iat = claims.get("exp"), claims.get("iat")
    if not isinstance(exp, int | float) or exp + CLOCK_SKEW < now:
        raise SignInError("expired")
    if isinstance(iat, int | float) and iat - CLOCK_SKEW > now:
        raise SignInError("not_yet_valid")
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SignInError("bad_nonce")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255:
        raise SignInError("malformed")
    email = claims.get("email")
    return GoogleIdentity(
        sub=sub,
        email=email if isinstance(email, str) else "",
        email_verified=claims.get("email_verified") is True,  # a string "true" is not Google's form
        name=" ".join(str(claims.get("name") or "").split())[:120],
    )
