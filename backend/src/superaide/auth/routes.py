"""HTTP side of accounts: /auth endpoints, the session cookie and request guards.

The session lives in an httpOnly, SameSite=Lax cookie, so page scripts can't read
it. Requests that change something must carry the X-Requested-With header: a
custom header forces the browser's CORS preflight, which only our frontend's
origin passes. That is the CSRF protection for cookie-authenticated calls.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from superaide.auth import service
from superaide.auth.service import AuthError, Client
from superaide.config import get_settings
from superaide.db.models import AgentHire, Onboarding, UserAccount
from superaide.db.session import session_scope

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def client_of(request: Request) -> Client:
    return Client(
        ip=(request.client.host if request.client else "")[:64], user_agent=request.headers.get("user-agent", "")
    )


def require_csrf_header(request: Request) -> None:
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


class LoginBody(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class PasswordBody(BaseModel):
    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


def make_router() -> APIRouter:
    router = APIRouter(prefix="/auth", dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)])

    @router.post("/signup")
    def signup(body: SignupBody, request: Request, response: Response) -> dict[str, Any]:
        client = client_of(request)

        def go(s: Any) -> tuple[str, dict[str, Any]]:
            user, token = service.signup(s, email=body.email, password=body.password, name=body.name, client=client)
            return token, user_view(s, user)

        token, view = run(go)
        set_session_cookie(response, token)
        return view

    @router.post("/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        client = client_of(request)

        def go(s: Any) -> tuple[str, dict[str, Any]]:
            user, token = service.login(s, email=body.email, password=body.password, client=client)
            return token, user_view(s, user)

        token, view = run(go)
        set_session_cookie(response, token)
        return view

    @router.post("/logout")
    def logout(request: Request, response: Response) -> dict[str, str]:
        client = client_of(request)
        run(lambda s: service.logout(s, session_token(request), client))
        clear_session_cookie(response)
        return {"status": "logged out"}

    @router.get("/me")
    def me(request: Request) -> dict[str, Any]:
        token = session_token(request)
        with session_scope() as s:
            user = service.user_for_token(s, token)
            if user is None:
                raise HTTPException(401, "Please log in.")
            return user_view(s, user)

    @router.post("/password")
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

    @router.post("/logout-everywhere")
    def logout_everywhere(
        request: Request, response: Response, user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        n = run(lambda s: service.revoke_all(s, s.get(UserAccount, user_id)))
        clear_session_cookie(response)
        return {"status": "signed out everywhere", "sessions": n}

    return router
