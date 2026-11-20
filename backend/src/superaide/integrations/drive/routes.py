"""/app/drive: connect the Google Drive capability and turn drafts on (a scope upgrade).

Owner only, CSRF-checked, per instance. Drive uses the same Google OAuth client and the same callback as Gmail and
Calendar (`/app/connections/google/callback`): the one-time state says it's a Drive sign-in, and `finish` here takes
over. Google is asked for `drive.readonly` first, and for `drive.file` (only files Ava makes herself) only when the
owner turns drafts on. Responses never carry a token. The reading limits live under `/app/drive/read-settings`.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.identity.vault import store
from superaide.identity.vault.store import runtime_settings
from superaide.integrations.drive import client as api
from superaide.integrations.drive import skill as drive
from superaide.integrations.gmail import client as oauth
from superaide.integrations.google import client as google
from superaide.platform.audit import audit
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import scope_or_default
from superaide.platform.tenancy.models import PlatformState
from superaide.skills.connector_settings import last_changed
from superaide.skills.models import InstalledSkill

BACK = "/dashboard/capabilities/drive"

log = logging.getLogger(__name__)


class DraftsBody(BaseModel):
    enabled: bool


def _pending_key(state: str) -> str:
    from superaide.app.connections import _pending_key as key

    return key(state)


def _redirect_uri(origin: str) -> str:
    from superaide.app.connections import google_redirect_uri

    return google_redirect_uri(origin)


def start_url(user_id: uuid.UUID, scopes: list[str], origin: str) -> str:
    """A Google sign-in URL asking for `scopes` on top of what was granted before."""
    settings = runtime_settings()
    if not (settings.google_client_id and settings.google_client_secret):
        raise HTTPException(409, "Save your Google OAuth client under Capabilities first.")
    verifier, challenge = google.pkce_pair()
    state = secrets.token_urlsafe(24)
    scope = scope_or_default()
    with session_scope() as s:
        for old in s.scalars(select(PlatformState).where(PlatformState.key.like("google_pending:%"))).all():
            if (old.value or {}).get("agent_id") == str(scope.agent_id):
                s.delete(old)  # only the latest Google sign-in per instance counts
        s.add(
            PlatformState(
                key=_pending_key(state),
                value={
                    "purpose": "drive", "scopes": scopes, "verifier": verifier, "user_id": str(user_id),
                    "at": datetime.now(UTC).isoformat(), "agent_id": str(scope.agent_id),
                    "tenant_id": str(scope.tenant_id), "origin": origin,
                },
            )
        )  # fmt: skip
    return oauth.auth_url(settings.google_client_id, _redirect_uri(origin), state, challenge, scopes)


def finish(code: str, pending: dict[str, Any]) -> RedirectResponse:
    """The Google callback for a Drive sign-in, in the instance that started it."""
    from superaide.app.connections import pending_origin

    origin = pending_origin(pending)  # re-checked at the callback; never a tampered or unregistered address
    back = f"{origin}{BACK}"
    settings = runtime_settings()
    wanted = [str(x) for x in pending.get("scopes") or [api.READONLY]]
    try:
        refresh, granted, _ = oauth.exchange_code(
            client_id=settings.google_client_id, client_secret=settings.google_client_secret, code=code,
            verifier=str(pending["verifier"]), redirect_uri=_redirect_uri(origin), wanted=wanted,
            client=oauth.http_client(),
        )  # fmt: skip
    except oauth.ScopeMissing:
        return RedirectResponse(f"{back}?drive=no_access", status_code=303)
    except google.ApiDisabled:
        return RedirectResponse(f"{back}?drive=api_disabled", status_code=303)
    except google.GoogleError as exc:
        log.warning("drive sign-in failed: %s", exc)  # the message names the status and Google's reason only
        return RedirectResponse(f"{back}?drive=failed", status_code=303)
    with session_scope() as s:
        store.save(
            s, "google", {"drive_refresh_token": refresh}, user_id=uuid.UUID(pending["user_id"]),
            meta={"scopes": granted, "client_id": settings.google_client_id},
        )  # fmt: skip
    return RedirectResponse(f"{back}?drive=connected", status_code=303)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/drive",
        tags=["Owner: Google Drive"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Google Drive capability status", description="Installed, connected and drafts.")
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return {**drive.status(s), "last_changed": last_changed(s, "drive_drafts", lambda d: ["drafts"])}

    @router.post(
        "/connect",
        summary="Connect Google Drive (read-only)",
        description="Returns a Google sign-in URL asking to read Drive (drive.readonly). Install the capability first.",
    )
    def connect(
        request: Request, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, str]:
        from superaide.app.connections import connect_origin

        origin = connect_origin(request)
        with session_scope() as s:
            if drive.installed(s) is None:
                raise HTTPException(409, "Install the Google Drive capability first.")
            scopes = sorted(drive.granted(s) | {api.READONLY})
        return {"url": start_url(user_id, scopes, origin)}

    @router.post(
        "/drafts",
        summary="Turn Drive drafts on or off",
        description=(
            "On: Ava may ask your approval to create a new Doc or Sheet and to add text to one she made. If Google "
            "hasn't allowed that yet, the answer carries `url`, a sign-in asking for drive.file too (files Ava makes "
            "herself only). Nothing is written without your yes."
        ),
    )
    def drafts(
        request: Request,
        body: DraftsBody,
        _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        from superaide.app.connections import connect_origin

        origin = connect_origin(request) if body.enabled else ""
        with session_scope() as s:
            if drive.installed(s) is None:
                raise HTTPException(409, "Install the Google Drive capability first.")
            row = s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == drive.SKILL_ID))
            assert row is not None
            row.settings = {**(row.settings or {}), "drafts": body.enabled}
            granted = drive.granted(s)
            audit(s, "skills", "drive_drafts", enabled=body.enabled)
        url = None
        if body.enabled and api.FILE not in granted:
            url = start_url(user_id, sorted(granted | {api.READONLY, api.FILE}), origin)
        with session_scope() as s:
            return {
                **drive.status(s),
                "url": url,
                "last_changed": last_changed(s, "drive_drafts", lambda d: ["drafts"]),
            }

    return router
