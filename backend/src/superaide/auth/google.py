"""Sign in with Google, on top of the accounts in service.py.

`start` makes a one-time sign-in: a random `state` for Google to send back, a PKCE verifier and a
nonce, all kept server-side under the state's hash, and tied to this browser by a short-lived
httpOnly cookie. Without that tie, someone could finish their own Google sign-in in another
person's browser and leave them signed in to the wrong account (login CSRF).

`finish` checks the state and the cookie, swaps the code, verifies the ID token
(connectors/google_signin.py) and then finds the account:

1. by the Google account id (`sub`), which is how a returning person is recognised;
2. else by the verified email, linking the Google account to the existing account. If that
   account's email was never verified, someone else may have registered the address first and
   know its password: the password is removed and every session ends, so only the person who
   controls the Google account gets in (they can set a password again by resetting it);
3. else a new account, with its email counted as verified.

A Google account whose email Google hasn't verified is refused. Every outcome is an AuthEvent,
and failures count toward the same per-device throttle as wrong passwords.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import account_audit
from superaide.auth import service
from superaide.auth.service import AuthError, Client
from superaide.config import get_settings
from superaide.connectors import google_signin
from superaide.connectors.google_signin import GoogleIdentity, SignInError
from superaide.db.models import UserAccount
from superaide.links import allowed_request_origin, same_origin
from superaide.tenancy.models import PlatformState

PENDING_TTL = timedelta(minutes=10)
PREFIX = "gsignin:"
COOKIE = "superaide_google_signin"
PAGES = ("login", "signup")  # where a sign-in started, so a failure can send the person back there


@dataclass(frozen=True)
class Started:
    url: str
    browser_secret: str  # goes in the cookie; only its hash is stored


def _key(state: str) -> str:
    return PREFIX + hashlib.sha256(state.encode()).hexdigest()[:48]  # fits platform_state.key (60)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def http_client() -> httpx.Client:
    """Replaced in tests with one that never reaches Google."""
    return httpx.Client(timeout=15)


def registered_origins() -> list[str]:
    """The origins whose callback address is registered with Google: FRONTEND_ORIGIN, then GOOGLE_REDIRECT_ORIGINS."""
    settings = get_settings()
    extra = [o.strip() for o in settings.google_redirect_origins.split(",") if o.strip()]
    return [settings.frontend_origin.rstrip("/"), *(o.rstrip("/") for o in extra)]


def origin_for(request: Request) -> str:
    """The origin to sign in on: where the person is using the app, so the one-time browser cookie
    set now is still there when Google sends them back. Google only accepts redirect addresses
    registered in its console, so that origin must be allowed (superaide/links.py) and registered
    (FRONTEND_ORIGIN or GOOGLE_REDIRECT_ORIGINS). Any other address gets a message, not a failed sign-in."""
    settings = get_settings()
    home = settings.frontend_origin.rstrip("/")
    used = allowed_request_origin(request, settings)
    if used is None or any(same_origin(used, o) for o in registered_origins()):
        return used or home
    raise AuthError(
        409,
        f"Google sign-in works only at {home}. Open the app there and try again, or use your email and password.",
    )


def redirect_uri(origin: str | None = None) -> str:
    """Through the web app's /api proxy, so the session cookie is set on the app's own origin."""
    return f"{(origin or get_settings().frontend_origin).rstrip('/')}/api/auth/google/callback"


def pending_origin(pending: dict[str, Any]) -> str:
    """The origin a pending sign-in started on, checked again: only a registered origin is ever used."""
    stored = str(pending.get("origin") or "")
    if stored and any(same_origin(stored, o) for o in registered_origins()):
        return stored.rstrip("/")
    return get_settings().frontend_origin.rstrip("/")


def safe_next(raw: str | None) -> str:
    """A path on this site to return to, or "". Never another site (`//evil`, `/\\evil`, `https:`)."""
    raw = (raw or "").strip()
    if not raw.startswith("/") or raw.startswith(("//", "/\\")) or len(raw) > 300:
        return ""
    if any(c in raw for c in "\\\r\n\t") or ":" in raw.split("?", 1)[0]:
        return ""
    return raw


def configured() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret)


def start(
    session: Session,
    *,
    client: Client,
    next_path: str | None,
    timezone: str | None,
    origin: str,
    page: str | None = None,
) -> Started:
    settings = get_settings()
    now = datetime.now(UTC)
    window = timedelta(minutes=15)
    if service._recent(session, "google_fail", window, ip=client.ip) >= settings.login_failures_per_ip:
        raise AuthError(429, "Too many attempts from this device. Wait 15 minutes and try again.")
    if service._recent(session, "google_start", window, ip=client.ip) >= settings.login_failures_per_ip * 3:
        raise AuthError(429, "Too many attempts from this device. Wait 15 minutes and try again.")
    # Sign-ins nobody finished: forget them, so unused starts don't pile up.
    for row in session.scalars(select(PlatformState).where(PlatformState.key.like(PREFIX + "%"))).all():
        at = (row.value or {}).get("at")
        if not at or now - datetime.fromisoformat(at) > PENDING_TTL:
            session.delete(row)
    state = secrets.token_urlsafe(32)
    browser = secrets.token_urlsafe(32)
    verifier, challenge = google_signin.pkce_pair()
    nonce = google_signin.nonce_for(secrets.token_urlsafe(32))
    session.add(
        PlatformState(
            key=_key(state),
            value={
                "verifier": verifier, "nonce": nonce, "browser": _hash(browser), "at": now.isoformat(),
                "next": safe_next(next_path), "timezone": service.valid_timezone(timezone),
                "origin": origin, "page": page if page in PAGES else "login",
            },
        )
    )  # fmt: skip
    service._event(session, "google_start", client)
    url = google_signin.auth_url(
        settings.google_client_id, redirect_uri(origin), state=state, challenge=challenge, nonce=nonce
    )
    return Started(url=url, browser_secret=browser)


def take_pending(session: Session, state: str, browser_secret: str | None) -> dict[str, Any]:
    """The sign-in this state belongs to, used up. Raises SignInError("expired") if there is none, it's
    old, or it was started in another browser."""
    row = session.get(PlatformState, _key(state)) if state else None
    if row is None:
        raise SignInError("expired")
    pending = dict(row.value or {})
    session.delete(row)  # one use only
    at = pending.get("at")
    if not at or datetime.now(UTC) - datetime.fromisoformat(at) > PENDING_TTL:
        raise SignInError("expired")
    if not browser_secret or not secrets.compare_digest(_hash(browser_secret), str(pending.get("browser", ""))):
        raise SignInError("expired")
    return pending


def identify(pending: dict[str, Any], code: str, http: httpx.Client) -> GoogleIdentity:
    settings = get_settings()
    id_token = google_signin.exchange_code(
        client_id=settings.google_client_id, client_secret=settings.google_client_secret, code=code,
        verifier=str(pending["verifier"]), redirect_uri=redirect_uri(pending_origin(pending)), client=http,
    )  # fmt: skip
    return google_signin.verify_id_token(
        id_token, client_id=settings.google_client_id, nonce=str(pending.get("nonce", "")), client=http
    )


def fail(session: Session, client: Client, reason: str, *, user_id: Any = None, email: str = "") -> None:
    service._event(session, "google_fail", client, user_id=user_id, email=email)
    account_audit(session, user_id, "google_signin_refused", reason=reason)


def sign_in(session: Session, who: GoogleIdentity, *, client: Client, timezone: str | None) -> tuple[UserAccount, str]:
    """The account for this Google identity (found, linked or created) and a new session token.
    Raises SignInError with a reason code. The caller records failures with `fail`."""
    now = datetime.now(UTC)
    if not who.email_verified:
        raise SignInError("unverified")
    try:
        email = service.normalize_email(who.email)
    except AuthError as exc:
        raise SignInError("unverified") from exc

    user = session.scalar(select(UserAccount).where(UserAccount.google_sub == who.sub))
    kind = "google_login_ok"
    if user is None:
        user = service.find_user(session, email)
        if user is not None:
            if user.google_sub and user.google_sub != who.sub:
                raise SignInError("failed")  # already tied to a different Google account
            if user.status != "active":
                raise SignInError("failed")
            if user.locked_until and user.locked_until > now:
                raise SignInError("locked")
            takeover_guard = user.email_verified_at is None
            if takeover_guard:
                # Whoever registered this address never proved they own it; the Google account does.
                user.password_hash = ""
                user.failed_logins = 0
                service.revoke_all(session, user)
                user.email_verified_at = now
            user.google_sub = who.sub
            kind = "google_linked"
            account_audit(session, user.id, "google_linked", password_removed=takeover_guard)
        else:
            if service.signups_throttled(session, client):
                raise SignInError("throttled")
            name = who.name or email.split("@")[0][:120]
            user = service.create_account(
                session, email=who.email, normalized=email, name=name, password_hash="", timezone=timezone,
                google_sub=who.sub, verified=True,
            )  # fmt: skip
            service._event(session, "signup", client, user_id=user.id, email=email)
            account_audit(session, user.id, "signup", via="google")
            kind = "google_signup"
    if user.status != "active":
        raise SignInError("failed")
    if user.locked_until and user.locked_until > now:
        raise SignInError("locked")
    user.failed_logins = 0
    user.last_login_at = now
    service._event(session, kind, client, user_id=user.id, email=email)
    return user, service.new_session(session, user, client)
