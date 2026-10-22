"""Sign-up, login, sessions and the protections around them.

- Passwords: Argon2id (see passwords.py). Unknown emails cost the same work.
- Sessions: a random 256-bit token in an httpOnly cookie; only SHA-256 is stored.
  Expiry slides to `session_days` after the last use; logout revokes on the server.
- Abuse limits: an account locks after `login_max_failures` wrong passwords, and
  one address can only fail so often. Every attempt is an AuthEvent row.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from superaide.audit import account_audit
from superaide.auth.passwords import hash_password, password_problem, verify_password
from superaide.config import get_settings
from superaide.db.models import AuthEvent, UserAccount, UserSession
from superaide.db.session import rows_affected

# Only refresh last_used_at/expiry this often, so reads don't write on every request.
TOUCH_EVERY = timedelta(minutes=5)


class AuthError(Exception):
    """A failure the person can act on. `status` maps to an HTTP status code."""

    def __init__(self, status: int, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.field = field


@dataclass(frozen=True)
class Client:
    ip: str = ""
    user_agent: str = ""


def _now() -> datetime:
    return datetime.now(UTC)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    """Validated, lower-cased address used for uniqueness. Raises AuthError if invalid."""
    try:
        result = validate_email(email.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise AuthError(422, "Enter a valid email address.", "email") from exc
    return result.normalized.lower()


def _event(session: Session, kind: str, client: Client, *, user_id=None, email: str = "") -> None:
    session.add(
        AuthEvent(kind=kind, user_id=user_id, email_normalized=email, ip=client.ip, user_agent=client.user_agent[:300])
    )


def _recent(session: Session, kind: str, since: timedelta, *, ip: str | None = None, user_id=None) -> int:
    q = select(func.count()).select_from(AuthEvent).where(AuthEvent.kind == kind, AuthEvent.created_at > _now() - since)
    if ip is not None:
        q = q.where(AuthEvent.ip == ip)
    if user_id is not None:
        q = q.where(AuthEvent.user_id == user_id)
    return session.scalar(q) or 0


def _phantom_lock(session: Session, email: str, now: datetime) -> datetime | None:
    """An email with no account locks exactly like a real one, so the lockout message
    can't be used to find out whether an address is registered."""
    settings = get_settings()
    window = timedelta(minutes=settings.login_lock_minutes)
    failures = session.scalars(
        select(AuthEvent.created_at)
        .where(AuthEvent.kind == "login_fail", AuthEvent.email_normalized == email, AuthEvent.created_at > now - window)
        .order_by(AuthEvent.created_at.desc())
    ).all()
    if len(failures) < settings.login_max_failures:
        return None
    return failures[0] + window


def find_user(session: Session, email: str) -> UserAccount | None:
    return session.scalar(select(UserAccount).where(UserAccount.email_normalized == email))


# --- sign-up and login -------------------------------------------------------------


def valid_timezone(name: str | None) -> str:
    """An IANA time zone name the server knows, or UTC."""
    name = (name or "").strip()
    if not name or len(name) > 64:
        return "UTC"
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return "UTC"
    return name


def signup(
    session: Session, *, email: str, password: str, name: str, client: Client, timezone: str | None = None
) -> tuple[UserAccount, str]:
    name = " ".join(name.split())[:120]
    if not name:
        raise AuthError(422, "Tell us your name.", "name")
    normalized = normalize_email(email)
    if signups_throttled(session, client):
        raise AuthError(429, "Too many new accounts from this device. Try again in an hour.")
    problem = password_problem(password, email=normalized, name=name)
    if problem:
        raise AuthError(422, problem, "password")
    if find_user(session, normalized) is not None:
        # Spend the same work as a real sign-up before answering.
        hash_password(password)
        raise AuthError(409, "There's already an account with this email. Log in instead.", "email")

    user = create_account(
        session, email=email, normalized=normalized, name=name, password_hash=hash_password(password),
        timezone=timezone,
    )  # fmt: skip
    _event(session, "signup", client, user_id=user.id, email=normalized)
    account_audit(session, user.id, "signup")
    return user, new_session(session, user, client)


def signups_throttled(session: Session, client: Client) -> bool:
    return _recent(session, "signup", timedelta(hours=1), ip=client.ip) >= get_settings().signups_per_ip_per_hour


def create_account(
    session: Session,
    *,
    email: str,
    normalized: str,
    name: str,
    password_hash: str,
    timezone: str | None,
    google_sub: str | None = None,
    verified: bool = False,
) -> UserAccount:
    """A new account. `password_hash` is "" for an account that signs in with Google only (no password
    matches it). It has no Person yet: a Person belongs to one agent instance, so the account gets its
    own when it hires (tenancy.join_as_owner), and an account that never hires is stored in none."""
    now = _now()
    user = UserAccount(
        email=email.strip()[:320],
        email_normalized=normalized,
        password_hash=password_hash,
        display_name=name,
        timezone=valid_timezone(timezone),
        password_changed_at=now if password_hash else None,
        last_login_at=now,
        google_sub=google_sub,
        email_verified_at=now if verified else None,
    )
    session.add(user)
    session.flush()
    return user


def login(session: Session, *, email: str, password: str, client: Client) -> tuple[UserAccount, str]:
    settings = get_settings()
    try:
        normalized = normalize_email(email)
    except AuthError:
        normalized = email.strip().lower()[:320]
    if _recent(session, "login_fail", timedelta(minutes=15), ip=client.ip) >= settings.login_failures_per_ip:
        raise AuthError(429, "Too many attempts from this device. Wait 15 minutes and try again.")

    user = find_user(session, normalized)
    now = _now()
    locked_until = user.locked_until if user is not None else _phantom_lock(session, normalized, now)
    if locked_until and locked_until > now:
        verify_password(None, password)  # same work either way
        minutes = max(1, int((locked_until - now).total_seconds() // 60) + 1)
        _event(session, "login_locked", client, user_id=user.id if user else None, email=normalized)
        raise AuthError(429, f"Too many wrong passwords. Try again in {minutes} minutes.")

    ok, needs_rehash = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or user.status != "active":
        if user is not None:
            # Count in the database, not in Python, so parallel attempts can't race past the limit.
            failures = session.scalar(
                update(UserAccount)
                .where(UserAccount.id == user.id)
                .values(failed_logins=UserAccount.failed_logins + 1)
                .returning(UserAccount.failed_logins)
            )
            if failures and failures >= settings.login_max_failures:
                session.execute(
                    update(UserAccount)
                    .where(UserAccount.id == user.id)
                    .values(locked_until=now + timedelta(minutes=settings.login_lock_minutes), failed_logins=0)
                )
                account_audit(session, user.id, "account_locked")
        _event(session, "login_fail", client, user_id=user.id if user else None, email=normalized)
        raise AuthError(401, "That email and password don't match.")

    if needs_rehash:
        user.password_hash = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    _event(session, "login_ok", client, user_id=user.id, email=normalized)
    return user, new_session(session, user, client)


# --- sessions ----------------------------------------------------------------------


def new_session(session: Session, user: UserAccount, client: Client) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=now + timedelta(days=get_settings().session_days),
            last_used_at=now,
            user_agent=client.user_agent[:300],
            ip=client.ip[:64],
        )
    )
    return token


def user_for_token(session: Session, token: str | None) -> UserAccount | None:
    """The signed-in account for a session cookie, sliding its expiry. None if not valid."""
    if not token:
        return None
    row = session.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    now = _now()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    user = session.get(UserAccount, row.user_id)
    if user is None or user.status != "active":
        return None
    if row.last_used_at is None or now - row.last_used_at > TOUCH_EVERY:
        row.last_used_at = now
        row.expires_at = now + timedelta(days=get_settings().session_days)
    return user


def logout(session: Session, token: str | None, client: Client) -> None:
    if not token:
        return
    row = session.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = _now()
        _event(session, "logout", client, user_id=row.user_id)


def revoke_all(session: Session, user: UserAccount, *, except_token: str | None = None) -> int:
    q = update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    if except_token:
        q = q.where(UserSession.token_hash != token_hash(except_token))
    return rows_affected(session.execute(q.values(revoked_at=_now())))


def change_email(session: Session, user: UserAccount, *, new_email: str, password: str, client: Client) -> None:
    """Move the account to a new address. Needs the password; the new address must be verified again,
    and links already sent to the old one stop working."""
    settings = get_settings()
    window = timedelta(minutes=settings.login_lock_minutes)
    if _recent(session, "email_change_fail", window, user_id=user.id) >= settings.login_max_failures:
        raise AuthError(429, f"Too many wrong passwords. Try again in {settings.login_lock_minutes} minutes.")
    ok, _ = verify_password(user.password_hash, password)
    if not ok:
        _event(session, "email_change_fail", client, user_id=user.id)
        raise AuthError(401, "Your password isn't right.", "password")
    normalized = normalize_email(new_email)
    if normalized == user.email_normalized:
        raise AuthError(422, "That's already your email.", "email")
    if find_user(session, normalized) is not None:
        raise AuthError(409, "There's already an account with this email.", "email")
    old = user.email_normalized
    user.email = new_email.strip()[:320]  # sets email_normalized too (db/models.UserAccount)
    user.email_verified_at = None
    _event(session, "email_changed", client, user_id=user.id, email=normalized)
    account_audit(session, user.id, "email_changed", old_domain=old.rsplit("@", 1)[-1])


def change_password(
    session: Session, user: UserAccount, *, current: str, new: str, keep_token: str | None, client: Client
) -> None:
    # Same limit as login, so a stolen session can't be used to guess the password.
    settings = get_settings()
    window = timedelta(minutes=settings.login_lock_minutes)
    if _recent(session, "password_change_fail", window, user_id=user.id) >= settings.login_max_failures:
        raise AuthError(429, f"Too many wrong passwords. Try again in {settings.login_lock_minutes} minutes.")
    ok, _ = verify_password(user.password_hash, current)
    if not ok:
        _event(session, "password_change_fail", client, user_id=user.id)
        raise AuthError(401, "Your current password isn't right.", "current")
    problem = password_problem(new, email=user.email_normalized, name=user.display_name)
    if problem:
        raise AuthError(422, problem, "password")
    user.password_hash = hash_password(new)
    user.password_changed_at = _now()
    revoke_all(session, user, except_token=keep_token)
    _event(session, "password_changed", client, user_id=user.id)
    account_audit(session, user.id, "password_changed")


def reset_password(session: Session, email: str, new: str) -> UserAccount:
    """For the operator at the terminal (no email server yet): set a password, sign out everywhere."""
    user = find_user(session, normalize_email(email))
    if user is None:
        raise AuthError(404, f"No account for {email}.")
    problem = password_problem(new, email=user.email_normalized, name=user.display_name)
    if problem:
        raise AuthError(422, problem)
    user.password_hash = hash_password(new)
    user.password_changed_at = _now()
    user.failed_logins = 0
    user.locked_until = None
    revoke_all(session, user)
    _event(session, "password_reset_cli", Client(ip="cli"), user_id=user.id)
    account_audit(session, user.id, "password_reset", via="cli")
    return user
