"""HTTP side of accounts: /auth endpoints, the session cookie and request guards.

The session lives in an httpOnly, SameSite=Lax cookie, so page scripts can't read
it. Requests that change something must carry the X-Requested-With header: a
custom header forces the browser's CORS preflight, which only our frontend's
origin passes. That is the CSRF protection for cookie-authenticated calls.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, Security
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select

from superaide.auth import emails, service
from superaide.auth import google as google_signin
from superaide.auth.service import AuthError, Client
from superaide.config import get_settings
from superaide.db.models import AgentHire, Onboarding, UserAccount
from superaide.db.session import session_scope
from superaide.mail import Mail, MailError, get_mailer

log = logging.getLogger(__name__)

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def client_of(request: Request) -> Client:
    return Client(
        ip=(request.client.host if request.client else "")[:64], user_agent=request.headers.get("user-agent", "")
    )


# Declared for /docs, so its Authorize dialog can send the header; the check below reads it itself.
csrf_header = APIKeyHeader(
    name="X-Requested-With",
    scheme_name="X-Requested-With",
    description="Any value. Required on every POST from a browser (CSRF protection).",
    auto_error=False,
)


def require_csrf_header(request: Request, _: str | None = Security(csrf_header)) -> None:
    if request.method in UNSAFE and not request.headers.get("x-requested-with"):
        raise HTTPException(403, "Missing X-Requested-With header")


# A JSON string escape for NUL (\u0000) that isn't itself an escaped backslash followed by "u0000".
_JSON_NUL = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000")


async def reject_nul_text(request: Request) -> None:
    """Postgres text can't hold NUL characters: refuse them up front instead of failing on save (a 500)."""
    content_type = request.headers.get("content-type", "")
    if request.method not in UNSAFE or content_type.startswith(("multipart/", "application/x-www-form-urlencoded")):
        return  # uploads are cleaned by the extractor; only JSON bodies are checked here
    if _JSON_NUL.search(await request.body()):
        raise HTTPException(422, "Text can't contain NUL characters.")


def session_token(request: Request) -> str | None:
    return request.cookies.get(get_settings().session_cookie_name)


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/", httponly=True, samesite="lax")


def run[T](fn: Callable[[Any], T]) -> T:
    """Run fn in a transaction. AuthErrors still commit what was recorded (failed
    attempts, lockouts) before turning into an HTTP error."""
    error: AuthError | None = None
    result: Any = None
    with session_scope() as s:
        try:
            result = fn(s)
        except AuthError as exc:
            error = exc
    if error is not None:
        detail: Any = error.message if error.field is None else {"message": error.message, "field": error.field}
        raise HTTPException(error.status, detail)
    return result


def current_user_id(request: Request) -> uuid.UUID:
    """The signed-in account, or 401."""
    token = session_token(request)
    with session_scope() as s:
        user = service.user_for_token(s, token)
        if user is None:
            raise HTTPException(401, "Please log in.")
        return user.id


def optional_user_id(request: Request) -> uuid.UUID | None:
    token = session_token(request)
    if not token:
        return None
    with session_scope() as s:
        user = service.user_for_token(s, token)
        return user.id if user else None


def user_view(s: Any, user: UserAccount) -> dict[str, Any]:
    hire = s.scalar(select(AgentHire).where(AgentHire.owner_user_id == user.id, AgentHire.status == "active"))
    onboarding = s.scalar(select(Onboarding).where(Onboarding.person_id == user.person_id)) if hire else None
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.display_name,
            "timezone": user.timezone,
            "email_verified": user.email_verified_at is not None,
            "has_password": bool(user.password_hash),
            "google": user.google_sub is not None,
            "created_at": user.created_at.isoformat(),
        },
        "hire": None
        if hire is None
        else {
            "agent": hire.agent_slug,
            "since": hire.created_at.isoformat(),
            "onboarding_done": bool(onboarding and onboarding.step == "done"),
        },
    }


class SignupBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    # The browser's IANA time zone (Intl.DateTimeFormat().resolvedOptions().timeZone); UTC if absent or unknown.
    timezone: str | None = Field(default=None, max_length=64)


class TokenBody(BaseModel):
    token: str = Field(min_length=16, max_length=200)


class ResetRequestBody(BaseModel):
    email: str = Field(min_length=1, max_length=320)


class ResetBody(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


def deliver(mail: Mail | None) -> None:
    """Send after the transaction committed. A failure is logged, never shown: the person can ask again."""
    if mail is None:
        return
    try:
        get_mailer().send(mail)
    except MailError:
        log.exception("couldn't send %r", mail.subject)


class LoginBody(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class GoogleStartBody(BaseModel):
    next: str | None = Field(default=None, max_length=300)  # where to go afterwards: a path on this site
    timezone: str | None = Field(default=None, max_length=64)  # for a new account, as at sign-up


def google_enabled() -> bool:
    return get_settings().google_signin_enabled


class PasswordBody(BaseModel):
    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


def make_router() -> APIRouter:
    router = APIRouter(
        prefix="/auth", tags=["Accounts"], dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)]
    )

    @router.post(
        "/signup",
        summary="Sign up",
        description=(
            "Creates an account and signs it in (sets the session cookie). Passwords need at least 10 characters."
        ),
    )
    def signup(body: SignupBody, request: Request, response: Response, background: BackgroundTasks) -> dict[str, Any]:
        client = client_of(request)

        def go(s: Any) -> tuple[str, dict[str, Any], Mail | None]:
            user, token = service.signup(
                s, email=body.email, password=body.password, name=body.name, client=client, timezone=body.timezone
            )
            return token, user_view(s, user), emails.start_verification(s, user)

        token, view, mail = run(go)
        background.add_task(deliver, mail)
        set_session_cookie(response, token)
        return view

    @router.post(
        "/login",
        summary="Log in",
        description=(
            "Signs in and sets the session cookie. Five wrong passwords lock the account for 15 minutes; repeated "
            "failures from one device are throttled."
        ),
    )
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        client = client_of(request)

        def go(s: Any) -> tuple[str, dict[str, Any]]:
            user, token = service.login(s, email=body.email, password=body.password, client=client)
            return token, user_view(s, user)

        token, view = run(go)
        set_session_cookie(response, token)
        return view

    @router.post("/logout", summary="Log out", description="Ends this session and clears the cookie.")
    def logout(request: Request, response: Response) -> dict[str, str]:
        client = client_of(request)
        run(lambda s: service.logout(s, session_token(request), client))
        clear_session_cookie(response)
        return {"status": "logged out"}

    @router.get("/me", summary="Who is signed in", description="The signed-in account, or `401` when there is none.")
    def me(request: Request) -> dict[str, Any]:
        token = session_token(request)
        with session_scope() as s:
            user = service.user_for_token(s, token)
            if user is None:
                raise HTTPException(401, "Please log in.")
            return user_view(s, user)

    @router.post(
        "/password", summary="Change password", description="Needs the current password. Other sessions are signed out."
    )
    def change_password(
        body: PasswordBody, request: Request, user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, str]:
        client = client_of(request)
        token = session_token(request)

        def go(s: Any) -> None:
            user = s.get(UserAccount, user_id)
            service.change_password(s, user, current=body.current, new=body.new, keep_token=token, client=client)

        run(go)
        return {"status": "password changed; other devices signed out"}

    @router.post(
        "/logout-everywhere",
        summary="Log out everywhere",
        description="Ends every session of this account, on every device.",
    )
    def logout_everywhere(
        request: Request, response: Response, user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        n = run(lambda s: service.revoke_all(s, s.get(UserAccount, user_id)))
        clear_session_cookie(response)
        return {"status": "signed out everywhere", "sessions": n}

    @router.post(
        "/verify-email/send",
        summary="Send the verification email again",
        description="Emails a new link that confirms the signed-in account's address. At most a few an hour.",
    )
    def send_verification(background: BackgroundTasks, user_id: uuid.UUID = Depends(current_user_id)) -> dict[str, str]:
        mail = run(lambda s: emails.start_verification(s, s.get(UserAccount, user_id)))
        background.add_task(deliver, mail)
        return {"status": "sent" if mail else "already verified"}

    @router.post(
        "/verify-email",
        summary="Verify an email address",
        description="Uses the token from the emailed link. Works without being signed in; each link works once.",
    )
    def verify_email(body: TokenBody, request: Request) -> dict[str, str]:
        client = client_of(request)
        run(lambda s: emails.verify_email(s, body.token, client))
        return {"status": "verified"}

    @router.post(
        "/password-reset/request",
        summary="Forgot password",
        description=(
            "Emails a reset link if the address has an account. The answer is the same either way, so it can't be "
            "used to find out who has signed up."
        ),
    )
    def request_reset(body: ResetRequestBody, request: Request, background: BackgroundTasks) -> dict[str, str]:
        client = client_of(request)
        mail = run(lambda s: emails.request_reset(s, body.email, client))
        background.add_task(deliver, mail)
        return {"status": "If that address has an account, we've emailed a link to reset the password."}

    @router.post(
        "/password-reset",
        summary="Reset password",
        description="Sets a new password with the token from the emailed link and signs out every device.",
    )
    def reset_password(body: ResetBody, request: Request) -> dict[str, str]:
        client = client_of(request)
        run(lambda s: emails.reset_password(s, body.token, body.password, client))
        return {"status": "password reset; log in with the new password"}

    @router.get(
        "/google",
        summary="Is Google sign-in available",
        description='`{"enabled": true}` when this server offers Continue with Google. Needs no session.',
    )
    def google_status() -> dict[str, bool]:
        return {"enabled": google_enabled() and google_signin.configured()}

    @router.post(
        "/google/start",
        summary="Start signing in with Google",
        description=(
            "Returns Google's sign-in URL (OpenID Connect with PKCE; scopes `openid email profile` only) and "
            "ties this sign-in to the browser with a short-lived cookie. Open the URL in the same browser. "
            "`404` while GOOGLE_SIGNIN_ENABLED is off."
        ),
    )
    def google_start(body: GoogleStartBody, request: Request, response: Response) -> dict[str, str]:
        if not google_enabled():
            raise HTTPException(404, "Not Found")
        if not google_signin.configured():
            raise HTTPException(503, "Google sign-in isn't set up on this server yet.")
        client = client_of(request)
        started = run(lambda s: google_signin.start(s, client=client, next_path=body.next, timezone=body.timezone))
        response.set_cookie(
            google_signin.COOKIE, started.browser_secret,
            max_age=int(google_signin.PENDING_TTL.total_seconds()), httponly=True,
            secure=get_settings().session_cookie_secure, samesite="lax", path="/",
        )  # fmt: skip
        return {"url": started.url}

    @router.get(
        "/google/callback",
        summary="Google sign-in callback",
        description=(
            "Google sends the browser here. Checks the one-time state and this browser's cookie, verifies "
            "Google's ID token, signs in (setting the session cookie) and sends the browser on. Failures go "
            "to /login?google=<reason>."
        ),
        include_in_schema=False,
    )
    def google_callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        if not google_enabled():
            raise HTTPException(404, "Not Found")
        front = get_settings().frontend_origin.rstrip("/")
        client = client_of(request)
        browser_secret = request.cookies.get(google_signin.COOKIE)

        def back(reason: str) -> RedirectResponse:
            out = RedirectResponse(f"{front}/login?google={reason}", status_code=303)
            out.delete_cookie(google_signin.COOKIE, path="/", httponly=True, samesite="lax")
            return out

        def pending_or_reason(s: Any) -> dict[str, Any] | str:
            try:
                return google_signin.take_pending(s, state, browser_secret)
            except google_signin.SignInError as exc:
                return exc.reason

        pending = run(pending_or_reason)  # used up even when Google reports an error
        if error or not code:
            return back("cancelled")
        if isinstance(pending, str):
            run(lambda s: google_signin.fail(s, client, "expired"))
            return back("expired")
        try:
            who = google_signin.identify(pending, code, google_signin.http_client())
        except google_signin.SignInError as exc:
            reason = exc.reason
            run(lambda s: google_signin.fail(s, client, reason))
            return back("failed")

        def go(s: Any) -> tuple[str, str] | str:
            try:
                user, token = google_signin.sign_in(s, who, client=client, timezone=pending.get("timezone"))
            except google_signin.SignInError as exc:
                google_signin.fail(s, client, exc.reason)
                return exc.reason
            return token, str(pending.get("next") or "")

        outcome = run(go)
        if isinstance(outcome, str):
            return back(outcome if outcome in {"unverified", "locked", "throttled"} else "failed")
        token, next_path = outcome
        out = RedirectResponse(f"{front}{next_path or '/login'}", status_code=303)
        set_session_cookie(out, token)
        out.delete_cookie(google_signin.COOKIE, path="/", httponly=True, samesite="lax")
        return out

    return router
