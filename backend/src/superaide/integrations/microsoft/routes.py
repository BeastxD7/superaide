"""/app/microsoft: connect Outlook mail (Microsoft 365) and turn sending on. Issue #668.

Owner only, CSRF-checked, per instance. The owner brings their own Entra app registration (client id and secret, kept in
the vault and shown only as hints); sign-in is the authorization-code flow with PKCE. Microsoft is asked for Mail.Read
first, and for Mail.Send only when the owner turns sending on (a second sign-in). The callback needs no session: the
one-time state made by the owner's own `connect` is the proof. Responses never carry a secret or a token. The limits
live under `/app/microsoft-mail/read-settings`.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.identity.vault import store
from superaide.integrations.google.client import pkce_pair
from superaide.integrations.microsoft import calendar_settings as cal_settings
from superaide.integrations.microsoft import client as api
from superaide.integrations.microsoft import mail, outlook_calendar
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import InstanceScope, scope_or_default, use_instance
from superaide.platform.tenancy.models import PlatformState
from superaide.skills.connector_settings import last_changed
from superaide.skills.models import InstalledSkill

log = logging.getLogger(__name__)

CALLBACK_PATH = "/api/app/microsoft/callback"
PENDING_TTL = timedelta(minutes=10)
BACK = "/dashboard/capabilities/outlook"


class ClientBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=64)
    client_secret: str = Field(min_length=1, max_length=300)


class SendingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _pending_key(state: str) -> str:
    return f"m365_pending:{hashlib.sha256(state.encode()).hexdigest()[:40]}"


def redirect_uri() -> str:
    return f"{get_settings().frontend_origin.rstrip('/')}{CALLBACK_PATH}"


def _view(s: Any) -> dict[str, Any]:
    return {**mail.status(s), "last_changed": last_changed(s, "m365_mail_settings", lambda d: ["sending"])}


def _row(s: Any) -> InstalledSkill:
    if mail.installed(s) is None:
        raise HTTPException(409, "Install the Outlook capability first.")
    row = s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == mail.SKILL_ID))
    assert row is not None
    return row


def start_url(user_id: uuid.UUID, scopes: list[str], purpose: str = "mail") -> str:
    with session_scope() as s:
        cid, _ = mail.credentials(s) if mail.client_saved(s) else ("", "")
    if not cid:
        raise HTTPException(409, "Save your Microsoft app (client id and secret) first.")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    scope = scope_or_default()
    with session_scope() as s:
        for old in s.scalars(select(PlatformState).where(PlatformState.key.like("m365_pending:%"))).all():
            if (old.value or {}).get("agent_id") == str(scope.agent_id):
                s.delete(old)  # only the latest sign-in per instance counts
        s.add(
            PlatformState(
                key=_pending_key(state),
                value={
                    "purpose": purpose, "scopes": scopes, "verifier": verifier, "user_id": str(user_id),
                    "at": datetime.now(UTC).isoformat(), "agent_id": str(scope.agent_id),
                    "tenant_id": str(scope.tenant_id),
                },
            )
        )  # fmt: skip
    return api.auth_url(cid, redirect_uri(), state, challenge, scopes)


def _finish(code: str, pending: dict[str, Any], back: str) -> RedirectResponse:
    wanted = [str(x) for x in pending.get("scopes") or api.READ_SCOPES]
    calendar = pending.get("purpose") == "calendar"
    back = f"{back.rsplit('/', 1)[0]}/outlook-calendar" if calendar else back
    try:
        with session_scope() as s:
            cid, secret = mail.credentials(s)
        refresh, granted = api.exchange_code(
            client_id=cid, client_secret=secret, code=code, verifier=str(pending["verifier"]),
            redirect_uri=redirect_uri(), wanted=wanted, client=api.http_client(),
        )  # fmt: skip
        access, refresh = api.refresh(
            client_id=cid, client_secret=secret, refresh_token=refresh, scopes=wanted, client=api.http_client()
        )
        address = api.me(api.http_client(), access)
    except api.ScopeMissing:
        return RedirectResponse(f"{back}?microsoft=no_access", status_code=303)
    except (api.MicrosoftError, mail.NotReady) as exc:
        log.warning("microsoft sign-in failed: %s", exc)  # the message names the status and Microsoft's reason only
        return RedirectResponse(f"{back}?microsoft=failed", status_code=303)
    with session_scope() as s:
        store.save(
            s, "microsoft", {"calendar_refresh_token" if calendar else "mail_refresh_token": refresh},
            user_id=uuid.UUID(pending["user_id"]),
            meta={"scopes": granted, "address": address},
        )  # fmt: skip
    return RedirectResponse(f"{back}?microsoft=connected", status_code=303)


def make_callback_router() -> APIRouter:
    router = APIRouter(prefix="/app/microsoft", tags=["Owner: Outlook"])

    @router.get("/callback", include_in_schema=False)
    def callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        # No session check: the one-time state, made by the owner's own /connect, is the proof.
        pending: dict[str, Any] = {}
        with session_scope() as s:
            row = s.get(PlatformState, _pending_key(state)) if state else None
            if row is not None:
                pending = dict(row.value or {})
                s.delete(row)  # one use only
        back = f"{get_settings().frontend_origin.rstrip('/')}{BACK}"
        if error or not code:
            return RedirectResponse(f"{back}?microsoft=cancelled", status_code=303)
        fresh = bool(pending.get("at")) and datetime.now(UTC) - datetime.fromisoformat(pending["at"]) < PENDING_TTL
        if not pending or not fresh or not pending.get("user_id") or not pending.get("agent_id"):
            return RedirectResponse(f"{back}?microsoft=expired", status_code=303)
        scope = InstanceScope(tenant_id=uuid.UUID(pending["tenant_id"]), agent_id=uuid.UUID(pending["agent_id"]))
        with use_instance(scope):  # the owner who started the sign-in, whoever's browser comes back
            return _finish(code, pending, back)

    return router


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/microsoft/mail",
        tags=["Owner: Outlook"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Outlook capability status", description="Installed, app saved, connected and sending.")
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return _view(s)

    @router.put(
        "/client",
        summary="Save your Microsoft app",
        description=(
            "The application (client) id and a client secret of an Entra app registration you made: redirect URI "
            "<app address>/api/app/microsoft/callback, delegated permissions Mail.Read, Mail.Send, offline_access and "
            "User.Read. Stored encrypted; the answer carries only hints."
        ),
    )
    def save_client(
        body: ClientBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        cid, secret = body.client_id.strip(), body.client_secret.strip()
        if not api.valid_client_id(cid):
            raise HTTPException(422, "The application (client) id looks like 00000000-0000-0000-0000-000000000000.")
        if not api.valid_secret(secret):
            raise HTTPException(422, "That doesn't look like a client secret value.")
        with session_scope() as s:
            if mail.installed(s) is None and outlook_calendar.installed(s) is None:
                raise HTTPException(409, "Install the Outlook mail or Outlook calendar capability first.")
            store.save(s, "microsoft", {"client_id": cid, "client_secret": secret}, user_id=user_id, meta={})
            store.remove_field(s, "microsoft", "mail_refresh_token")  # a new app means a new sign-in
            return _view(s)

    @router.post(
        "/connect",
        summary="Connect Outlook mail (read-only)",
        description="Returns a Microsoft sign-in URL asking to read mail (Mail.Read). Save your app first.",
    )
    def connect(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, str]:
        with session_scope() as s:
            _row(s)
            scopes = list(api.READ_SCOPES) + ([api.SEND_SCOPE] if mail.sending_wanted(s) else [])
        return {"url": start_url(user_id, scopes)}

    @router.delete("", summary="Disconnect Outlook", description="Deletes the stored sign-in (the app stays saved).")
    def disconnect(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            store.remove_field(s, "microsoft", "mail_refresh_token")
            audit(s, "connections", "disconnected", provider="microsoft", user_id=user_id)
            return _view(s)

    @router.post(
        "/sending",
        summary="Turn Outlook sending on or off",
        description=(
            "On: Ava may ask your approval to reply to an email or send a new one. If Microsoft hasn't allowed "
            "Mail.Send yet, the answer carries `url`, a sign-in asking for it. Nothing is sent without your yes."
        ),
    )
    def sending(
        body: SendingBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            row = _row(s)
            row.settings = {**(row.settings or {}), "sending": body.enabled}
            audit(s, "skills", "m365_mail_settings", sending=body.enabled)
            needs = body.enabled and mail.connected(s) and api.SEND_SCOPE not in mail.granted(s)
        url = start_url(user_id, [*api.READ_SCOPES, api.SEND_SCOPE]) if needs else None
        with session_scope() as s:
            return {**_view(s), "url": url}

    return router


class ChangesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def make_calendar_router(get_agent: Callable[[], Any]) -> APIRouter:
    """The Outlook calendar capability: it shares the saved Microsoft app with the mail capability."""
    router = APIRouter(
        prefix="/app/microsoft/calendar",
        tags=["Owner: Outlook calendar"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def view(s: Any) -> dict[str, Any]:
        return {
            **outlook_calendar.status(s),
            "last_changed": last_changed(s, "m365_calendar_settings", lambda d: ["changes"]),
        }

    def row(s: Any) -> InstalledSkill:
        if outlook_calendar.installed(s) is None:
            raise HTTPException(409, "Install the Outlook calendar capability first.")
        found = s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == cal_settings.SKILL_ID))
        assert found is not None
        return found

    @router.get("", summary="Outlook calendar status", description="Installed, app saved, connected and changes.")
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return view(s)

    @router.post(
        "/connect",
        summary="Connect the Outlook calendar (read-only)",
        description="Returns a Microsoft sign-in URL asking to read the calendar (Calendars.Read). Save the app first.",
    )
    def connect(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, str]:
        with session_scope() as s:
            row(s)
            scopes = list(api.CAL_READ_SCOPES) + ([api.CAL_WRITE_SCOPE] if outlook_calendar.changes_wanted(s) else [])
        return {"url": start_url(user_id, scopes, "calendar")}

    @router.delete("", summary="Disconnect the Outlook calendar", description="Deletes the stored sign-in.")
    def disconnect(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            store.remove_field(s, "microsoft", "calendar_refresh_token")
            audit(s, "connections", "disconnected", provider="microsoft-calendar", user_id=user_id)
            return view(s)

    @router.post(
        "/changes",
        summary="Turn Outlook calendar changes on or off",
        description=(
            "On: Ava may ask your approval to add an event (no invitations are sent). If Microsoft hasn't allowed "
            "Calendars.ReadWrite yet, the answer carries `url`, a sign-in asking for it."
        ),
    )
    def changes(
        body: ChangesBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            found = row(s)
            found.settings = {**(found.settings or {}), "changes": body.enabled}
            audit(s, "skills", "m365_calendar_settings", changes=body.enabled)
            needs = (
                body.enabled
                and outlook_calendar.connected(s)
                and api.CAL_WRITE_SCOPE not in outlook_calendar.granted(s)
            )
        url = start_url(user_id, [*api.CAL_READ_SCOPES, api.CAL_WRITE_SCOPE], "calendar") if needs else None
        with session_scope() as s:
            return {**view(s), "url": url}

    return router
