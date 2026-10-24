"""/app/gmail: connect the Gmail capability, turn drafts on (a scope upgrade), and tell Ava which
email address belongs to which of the owner's contacts.

Owner only, CSRF-checked, per instance. Gmail uses the same Google OAuth client and the same
callback as Calendar (`/app/connections/google/callback`): the one-time state says it's a Gmail
sign-in, and `finish` here takes over. Google is asked for `gmail.readonly` first, and for
`gmail.compose` only when the owner turns drafts on (`include_granted_scopes`, so earlier grants
stay). Responses never carry a token.
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
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.identity.trust import Trust
from superaide.integrations.gmail import client as api
from superaide.integrations.gmail import settings as gmail_settings
from superaide.integrations.gmail import skill as gmail
from superaide.integrations.google import client as google
from superaide.platform.db.models import ChannelIdentity, Contact, InstalledSkill
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import scope_or_default
from superaide.platform.tenancy.models import PlatformState
from superaide.skills.connector_settings import last_changed
from superaide.vault import store
from superaide.vault.store import runtime_settings

BACK = "/dashboard/capabilities"

log = logging.getLogger(__name__)


class DraftsBody(BaseModel):
    enabled: bool


class ReadSettingsBody(BaseModel):
    """Any subset of the reading settings. `codes: read` needs the account password (step-up)."""

    payment_preview: str | None = None
    bank_change_preview: str | None = None
    numbers: str | None = None
    codes: str | None = None
    body_chars: int | None = None
    search_limit: int | None = None
    thread_depth: int | None = None
    lookback_days: int | None = None
    blocked: list[str] | None = None
    allowed: list[str] | None = None
    confirm_password: str | None = None


class AddressBody(BaseModel):
    person_id: uuid.UUID
    email: EmailStr = Field(max_length=254)


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
        raise HTTPException(409, "Save your Google OAuth client under Connections first.")
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
                    "purpose": "gmail", "scopes": scopes, "verifier": verifier, "user_id": str(user_id),
                    "at": datetime.now(UTC).isoformat(), "agent_id": str(scope.agent_id),
                    "tenant_id": str(scope.tenant_id), "origin": origin,
                },
            )
        )  # fmt: skip
    return api.auth_url(settings.google_client_id, _redirect_uri(origin), state, challenge, scopes)


def finish(code: str, pending: dict[str, Any]) -> RedirectResponse:
    """The Google callback for a Gmail sign-in, in the instance that started it."""
    from superaide.app.connections import pending_origin

    origin = pending_origin(pending)  # re-checked at the callback; never a tampered or unregistered address
    back = f"{origin}{BACK}"
    settings = runtime_settings()
    wanted = [str(x) for x in pending.get("scopes") or [api.READONLY]]
    client = api.http_client()
    try:
        refresh, granted, access = api.exchange_code(
            client_id=settings.google_client_id, client_secret=settings.google_client_secret, code=code,
            verifier=str(pending["verifier"]), redirect_uri=_redirect_uri(origin), wanted=wanted, client=client,
        )  # fmt: skip
        address = str(api.profile(client, access).get("emailAddress") or "").lower() if access else ""
    except api.ScopeMissing:
        return RedirectResponse(f"{back}?gmail=no_access", status_code=303)
    except google.ApiDisabled:
        return RedirectResponse(f"{back}?gmail=api_disabled", status_code=303)
    except google.GoogleError as exc:
        log.warning("gmail sign-in failed: %s", exc)  # the message names the status and Google's reason only
        return RedirectResponse(f"{back}?gmail=failed", status_code=303)
    with session_scope() as s:
        store.save(
            s, "google", {"gmail_refresh_token": refresh}, user_id=uuid.UUID(pending["user_id"]),
            meta={"scopes": granted, "client_id": settings.google_client_id, "address": address},
        )  # fmt: skip
    return RedirectResponse(f"{back}?gmail=connected", status_code=303)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/gmail",
        tags=["Owner: Gmail"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Gmail capability status", description="Installed, connected, drafts, and the address.")
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return {**gmail.status(s), "last_changed": last_changed(s, "gmail_drafts", lambda d: ["drafts"])}

    @router.post(
        "/connect",
        summary="Connect Gmail (read-only)",
        description="Returns a Google sign-in URL asking to read Gmail (gmail.readonly). Install the capability first.",
    )
    def connect(
        request: Request, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict:
        from superaide.app.connections import connect_origin

        origin = connect_origin(request)
        with session_scope() as s:
            if gmail.installed(s) is None:
                raise HTTPException(409, "Install the Gmail capability first.")
            scopes = sorted(gmail.granted(s) | {api.READONLY})
        return {"url": start_url(user_id, scopes, origin)}

    @router.post(
        "/drafts",
        summary="Turn Gmail drafts on or off",
        description=(
            "On: Ava may save reply drafts in your Gmail. If Google hasn't allowed that yet, the answer carries "
            "`url`, a sign-in asking for gmail.compose too; drafts work once you've allowed it."
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
            if gmail.installed(s) is None:
                raise HTTPException(409, "Install the Gmail capability first.")
            row = s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == gmail.SKILL_ID))
            assert row is not None
            row.settings = {**(row.settings or {}), "drafts": body.enabled}
            granted = gmail.granted(s)
            from superaide.platform.audit import audit

            audit(s, "skills", "gmail_drafts", enabled=body.enabled)
        url = None
        if body.enabled and api.COMPOSE not in granted:
            url = start_url(user_id, sorted(granted | {api.READONLY, api.COMPOSE}), origin)
        with session_scope() as s:
            return {
                **gmail.status(s),
                "url": url,
                "last_changed": last_changed(s, "gmail_drafts", lambda d: ["drafts"]),
            }

    def _read_view(s: Any) -> dict[str, Any]:
        cfg = gmail_settings.load(s)
        return {
            "last_changed": last_changed(s, "gmail_read_settings", lambda d: d.get("changed", [])),
            "settings": cfg.as_dict(),
            "choices": {
                k: {"values": list(v[0]), "default": v[1], "label": v[2], "help": v[3]}
                for k, v in gmail_settings.CHOICES.items()
            },
        }

    @router.get(
        "/read-settings",
        summary="What Ava may read in your Gmail",
        description="The owner's reading settings (payment mail, numbers, codes, sizes, lookback, never-read and "
        "always-allowed lists) with every allowed value and its explanation.",
    )
    def read_settings(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return _read_view(s)  # the defaults until Gmail is installed

    @router.put(
        "/read-settings",
        summary="Change what Ava may read in your Gmail",
        description="Changes any of the reading settings. Each change is audited. Turning on one-time codes and "
        "password resets needs `confirm_password`; mail about them stays hidden otherwise.",
    )
    def put_read_settings(
        body: ReadSettingsBody,
        _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        from superaide.auth.passwords import verify_password
        from superaide.platform.audit import audit
        from superaide.platform.db.models import UserAccount

        with session_scope() as s:
            if gmail.installed(s) is None:
                raise HTTPException(409, "Install the Gmail capability first.")
            row = s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == gmail.SKILL_ID))
            assert row is not None
            before = gmail_settings.load(s)
            raw = before.as_dict()
            sent = body.model_dump(exclude={"confirm_password"}, exclude_none=True)
            for name, value in sent.items():
                if name in gmail_settings.CHOICES and value not in gmail_settings.CHOICES[name][0]:
                    raise HTTPException(422, f"{name}: choose one of {list(gmail_settings.CHOICES[name][0])}")
                if name in gmail_settings.LISTS:
                    cleaned = [gmail_settings.clean_entry(x) for x in value]
                    if any(not c for c in cleaned):
                        raise HTTPException(422, f"{name}: use an address, a domain or label:<name>")
                    value = list(dict.fromkeys(cleaned))[: gmail_settings.MAX_ENTRIES]
                raw[name] = value
            if raw["codes"] == "read" and before.codes != "read":
                user = s.get(UserAccount, user_id)
                if not body.confirm_password or user is None or not user.password_hash:
                    raise HTTPException(403, "Enter your password to let Ava read one-time codes and password resets.")
                ok, _rehash = verify_password(user.password_hash, body.confirm_password)
                if not ok:
                    audit(s, "skills", "gmail_read_setting_refused", setting="codes", reason="wrong password")
                    s.commit()
                    raise HTTPException(403, "Your password isn't right.")  # not 401: the app would sign the owner out
            row.settings = {**(row.settings or {}), "read": raw}
            after = gmail_settings.load(s)
            changed = sorted(k for k, v in after.as_dict().items() if v != before.as_dict()[k])
            if changed:
                audit(
                    s, "skills", "gmail_read_settings", changed=changed,
                    values={k: after.as_dict()[k] for k in changed if k not in gmail_settings.LISTS},
                    list_sizes={k: len(after.as_dict()[k]) for k in changed if k in gmail_settings.LISTS},
                )  # fmt: skip
            return _read_view(s)

    @router.post(
        "/addresses",
        summary="Say which email address is one of your contacts",
        description=(
            "Mail from this address, when Gmail confirms the sender (DMARC), is from that contact: triage can "
            "then treat them as known (and as a VIP, if they are one)."
        ),
    )
    def address(body: AddressBody, owner_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        email = str(body.email).strip().lower()
        with session_scope() as s:
            contact = s.scalar(
                select(Contact).where(
                    Contact.owner_person_id == owner_id, Contact.person_id == body.person_id,
                    Contact.removed_at.is_(None),
                )
            )  # fmt: skip
            if contact is None:
                raise HTTPException(404, "No such contact")
            found = s.scalar(
                select(ChannelIdentity).where(ChannelIdentity.provider == "email", ChannelIdentity.stable_key == email)
            )
            if found is not None and found.person_id not in (None, body.person_id):
                raise HTTPException(409, "That address already belongs to someone else.")
            if found is None:
                found = ChannelIdentity(provider="email", stable_key=email, aux_handles={})
                s.add(found)
            found.person_id, found.trust_level, found.lifecycle = body.person_id, int(Trust.LINKED), "active"
            found.verification = {"method": "owner_entered", "at": datetime.now(UTC).isoformat()}
            s.flush()
            from superaide.platform.audit import audit

            audit(s, "skills", "email_address_linked", person_id=body.person_id, identity_id=found.id)
            return {"person_id": str(body.person_id), "email": email}

    return router
