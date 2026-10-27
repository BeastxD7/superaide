"""One step-up service for every sensitive change (issue #451).

A sensitive setting (letting Ava read one-time codes, for example) is confirmed in two steps, so a stray or
scripted request cannot flip it:

1. the app asks for a confirmation (`issue`): a single-use nonce, valid for two minutes, bound to the signed-in
   session and to the exact action;
2. the app sends the change with that nonce (`consume`). The nonce is deleted atomically on first use, so it works
   once; a different session, a different action, a different account or an expired nonce is refused.

The default method is the owner's own confirmation in a dialog (a warning, an example, "I understand"). A stricter
mode (`STEPUP_MODE=password`) also asks for the account password, for accounts that have one; an account that
signs in with Google only has no password and keeps the dialog, so nobody is ever asked for a password they do
not have. Every request, confirmation and refusal is audited. Failures are 403, never 401 (the app would sign the
owner out). A dialog does not protect against someone who already controls a signed-in session; it protects against
accidents and one-call scripted changes.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from superaide.identity.auth.passwords import verify_password
from superaide.platform.audit import account_audit
from superaide.platform.config import get_settings
from superaide.platform.db.models import AgentState, UserAccount
from superaide.platform.tenancy.context import scope_or_default
from superaide.policy.killswitch import set_state

TTL = timedelta(minutes=2)
# The actions that need a step-up, each with what the owner is told. Anything else is refused.
ACTIONS: dict[str, str] = {
    "gmail_codes_read": "Let Ava read one-time codes and password resets in your Gmail",
}


class StepUpError(Exception):
    """A refused step-up. Always answered 403, so the app does not sign the owner out."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _digest(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


def _key(nonce: str) -> str:
    return f"su:{_digest(nonce)[:48]}"  # agent_state.key is 60 characters


def method_for(user: UserAccount | None) -> str:
    """ "confirm" (the dialog) or "password" (stricter mode, only for an account that has a password)."""
    if user is not None and user.password_hash and get_settings().stepup_mode == "password":
        return "password"
    return "confirm"


def issue(session: Session, *, user: UserAccount, session_token: str | None, action: str) -> dict[str, Any]:
    if action not in ACTIONS:
        raise StepUpError("That change doesn't need a confirmation.", "unknown_action")
    nonce = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + TTL
    set_state(
        session,
        _key(nonce),
        {"user": str(user.id), "session": _digest(session_token), "action": action, "expires": expires.isoformat()},
    )
    account_audit(session, user.id, "stepup_requested", change=action, method=method_for(user))
    return {"nonce": nonce, "expires_in": int(TTL.total_seconds()), "method": method_for(user), "action": action}


def consume(
    session: Session,
    *,
    user: UserAccount,
    session_token: str | None,
    action: str,
    nonce: str | None,
    password: str | None = None,
) -> None:
    """Accept the change, or raise StepUpError. The nonce is spent either way."""

    def refuse(message: str, reason: str) -> StepUpError:
        account_audit(session, user.id, "stepup_refused", change=action, reason=reason)
        return StepUpError(message, reason)

    if not nonce:
        raise refuse("Confirm this change first.", "no_confirmation")
    row = session.execute(
        delete(AgentState)
        .where(
            AgentState.tenant_id == scope_or_default().tenant_id,
            AgentState.agent_id == scope_or_default().agent_id,
            AgentState.key == _key(nonce),
        )
        .returning(AgentState.value)
    ).first()  # atomic: of two requests with the same nonce only one gets the row
    if row is None:
        raise refuse("That confirmation was already used or has expired. Confirm again.", "unknown_or_used")
    held: dict[str, Any] = row[0] or {}
    if held.get("expires", "") < datetime.now(UTC).isoformat():
        raise refuse("That confirmation has expired. Confirm again.", "expired")
    if held.get("user") != str(user.id):
        raise refuse("That confirmation isn't yours.", "other_account")
    if held.get("session") != _digest(session_token):
        raise refuse("That confirmation was made in another session. Confirm again.", "other_session")
    if held.get("action") != action:
        raise refuse("That confirmation was for a different change.", "other_action")
    if method_for(user) == "password":
        if not password:
            raise refuse("Enter your password to confirm.", "password_missing")
        ok, _rehash = verify_password(user.password_hash, password)
        if not ok:
            raise refuse("Your password isn't right.", "password_wrong")
    account_audit(session, user.id, "stepup_confirmed", change=action, method=method_for(user))
