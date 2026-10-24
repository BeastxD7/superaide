"""Email links: verifying an address, and resetting a forgotten password.

- Each link holds a random 256-bit token; only its SHA-256 is stored, it works
  once and expires (`verify_link_hours`, `reset_link_minutes`).
- The token travels in the URL fragment (`#token=`), which browsers never send to
  a server, so it stays out of proxy logs and Referer headers.
- Asking for a reset answers the same whether or not the address has an account,
  so the form can't be used to find out who has signed up.
- Functions return the Mail to send; the routes send it after the transaction
  commits, so a slow mail server never holds a database transaction open.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from superaide.auth.models import EmailToken
from superaide.auth.passwords import hash_password, password_problem
from superaide.auth.service import (
    AuthError,
    Client,
    _event,
    _recent,
    find_user,
    normalize_email,
    revoke_all,
    token_hash,
)
from superaide.mail import Mail
from superaide.platform.audit import account_audit
from superaide.platform.config import get_settings
from superaide.platform.db.models import UserAccount
from superaide.platform.db.session import rows_affected

VERIFY = "verify_email"
RESET = "reset_password"
EXPIRED = "This link has expired or was already used. Ask for a new one."


def _now() -> datetime:
    return datetime.now(UTC)


def _link(path: str, token: str, base: str | None = None) -> str:
    """`base`: the allowed origin the person is using (superaide.links); else the server's own address."""
    settings = get_settings()
    origin = base or settings.public_base_url or settings.frontend_origin
    return f"{origin.rstrip('/')}{path}#token={token}"


def _issued_recently(session: Session, user: UserAccount) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(EmailToken)
            .where(EmailToken.user_id == user.id, EmailToken.created_at > _now() - timedelta(hours=1))
        )
        or 0
    )


def _issue(session: Session, user: UserAccount, purpose: str, ttl: timedelta) -> str:
    # A new link replaces any earlier one for the same purpose.
    session.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=_now())
    )
    token = secrets.token_urlsafe(32)
    session.add(
        EmailToken(
            user_id=user.id,
            purpose=purpose,
            email_normalized=user.email_normalized,
            token_hash=token_hash(token),
            expires_at=_now() + ttl,
        )
    )
    session.flush()
    return token


def _consume(session: Session, token: str, purpose: str, *, mark: bool = True) -> tuple[EmailToken, UserAccount]:
    row = session.scalar(
        select(EmailToken).where(EmailToken.token_hash == token_hash(token), EmailToken.purpose == purpose)
    )
    now = _now()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise AuthError(400, EXPIRED)
    user = session.get(UserAccount, row.user_id)
    if user is None or user.status != "active" or user.email_normalized != row.email_normalized:
        raise AuthError(400, EXPIRED)  # the address changed since the link was sent
    if mark:
        _use(session, row)
    return row, user


def _use(session: Session, row: EmailToken) -> None:
    """Mark the link used, atomically: of two requests racing with one link, only one gets through."""
    done = session.execute(
        update(EmailToken).where(EmailToken.id == row.id, EmailToken.used_at.is_(None)).values(used_at=_now())
    )
    if rows_affected(done) != 1:
        raise AuthError(400, EXPIRED)


# --- verifying the address ------------------------------------------------------------


def ensure_can_send(session: Session, user: UserAccount) -> None:
    """Raises the hourly-limit error (429) if this account has had its share of email links. Call it
    before changing anything that a link would follow."""
    if _issued_recently(session, user) >= get_settings().email_links_per_account_per_hour:
        raise AuthError(429, "We've sent several emails already. Check your inbox, or try again in an hour.")


def start_verification(session: Session, user: UserAccount, base: str | None = None) -> Mail | None:
    """The email that verifies this account's address, or None if it is already verified."""
    if user.email_verified_at is not None:
        return None
    ensure_can_send(session, user)
    hours = get_settings().verify_link_hours
    token = _issue(session, user, VERIFY, timedelta(hours=hours))
    account_audit(session, user.id, "verification_sent")
    return Mail(
        to=user.email,
        subject="Confirm your email for Superaide",
        text=(
            f"Hi {user.display_name},\n\n"
            "Please confirm this is your email address by opening this link:\n\n"
            f"{_link('/verify-email', token, base)}\n\n"
            f"The link works once and for {hours} hours. If you didn't sign up for Superaide, ignore this email.\n"
        ),
    )


def verify_email(session: Session, token: str, client: Client) -> UserAccount:
    _, user = _consume(session, token, VERIFY)
    if user.email_verified_at is None:
        user.email_verified_at = _now()
    _event(session, "email_verified", client, user_id=user.id, email=user.email_normalized)
    account_audit(session, user.id, "email_verified")
    return user


# --- resetting a forgotten password -------------------------------------------------------


def request_reset(session: Session, email: str, client: Client, base: str | None = None) -> Mail | None:
    """The reset email, or None when there is nothing to send. The caller answers the same either way."""
    settings = get_settings()
    if _recent(session, "reset_requested", timedelta(minutes=15), ip=client.ip) >= settings.login_failures_per_ip:
        raise AuthError(429, "Too many requests from this device. Wait 15 minutes and try again.")
    try:
        normalized = normalize_email(email)
    except AuthError:
        normalized = ""
    user = find_user(session, normalized) if normalized else None
    _event(session, "reset_requested", client, user_id=user.id if user else None, email=normalized)
    if user is None or user.status != "active":
        return None
    if _issued_recently(session, user) >= settings.email_links_per_account_per_hour:
        return None  # quietly: an error here would confirm the account exists
    minutes = settings.reset_link_minutes
    token = _issue(session, user, RESET, timedelta(minutes=minutes))
    account_audit(session, user.id, "password_reset_sent")
    return Mail(
        to=user.email,
        subject="Reset your Superaide password",
        text=(
            f"Hi {user.display_name},\n\n"
            "Someone asked to reset the password for your Superaide account. To choose a new one, open:\n\n"
            f"{_link('/reset-password', token, base)}\n\n"
            f"The link works once and for {minutes} minutes. If it wasn't you, ignore this email; "
            "your password stays the same.\n"
        ),
    )


def reset_password(session: Session, token: str, new: str, client: Client) -> UserAccount:
    # A weak password is refused before the link is used up, so they can try another.
    row, user = _consume(session, token, RESET, mark=False)
    problem = password_problem(new, email=user.email_normalized, name=user.display_name)
    if problem:
        raise AuthError(422, problem, "password")
    _use(session, row)
    now = _now()
    user.password_hash = hash_password(new)
    user.password_changed_at = now
    user.failed_logins = 0
    user.locked_until = None
    if user.email_verified_at is None:
        user.email_verified_at = now  # opening the link proved they read this inbox
    revoke_all(session, user)
    _event(session, "password_reset", client, user_id=user.id, email=user.email_normalized)
    account_audit(session, user.id, "password_reset", via="email")
    return user
