"""The owner's push devices (ADR 0011 M2): the VAPID public key, subscribe, unsubscribe, the device
list, per-device settings and a test push.

Everything here answers 404 unless PUSH_ENABLED is on (and the VAPID pair exists, made on first
use). Every route needs the owner's session and the CSRF header, like the rest of /app. A
subscription is bound to the browser session that made it (`session_id`), so signing that session
out can remove it (ADR 0011 M5, in auth/). Rows belong to the owner's instance: Row-Level Security
and the person filter keep everyone else's devices invisible.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from superaide.app.deps import owner_person_id
from superaide.features.push import payload, send, vapid
from superaide.features.push.models import PushSend, PushSubscription
from superaide.features.push.subscriptions import BadSubscription, Secret, remove, upsert
from superaide.identity.auth.routes import current_user_id, reject_nul_text, require_csrf_header, session_token
from superaide.identity.auth.service import token_hash
from superaide.platform.config import get_settings
from superaide.platform.db.models import UserSession
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import scope_or_default

Platform = Literal["ios", "ipados", "android", "desktop", "other"]
TESTS_PER_HOUR = 5


async def require_push(person_id: uuid.UUID = Depends(owner_person_id)) -> AsyncIterator[None]:
    """404 while push is off, so the feature doesn't exist until it's turned on. Checked after the
    owner check (401 signed out, 403 not the owner), like every other owner route."""
    if not get_settings().push_enabled:
        raise HTTPException(404, "Not Found")
    if await run_in_threadpool(_keys_ready) is False:
        raise HTTPException(404, "Not Found")
    yield


def _keys_ready() -> bool:
    with session_scope() as s:
        return vapid.ensure(s) is not None


def _b64url(value: str, size: int, what: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, f"{what} isn't valid.") from exc
    if len(raw) != size or (size == 65 and raw[0] != 4):
        raise HTTPException(422, f"{what} isn't valid.")
    return value


class Keys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=100)
    auth: str = Field(min_length=16, max_length=30)


class SubscribeBody(BaseModel):
    endpoint: str = Field(min_length=12, max_length=1024)
    keys: Keys
    platform: Platform = "other"
    label: str = Field(default="", max_length=80)


class EndpointBody(BaseModel):
    endpoint: str = Field(min_length=12, max_length=1024)


class DeviceBody(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    preview_level: Literal["none", "sender", "reason"] | None = None
    mute_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 366)  # 0 unmutes


def _session_id(request: Request) -> uuid.UUID | None:
    token = session_token(request)
    if not token:
        return None
    with session_scope() as s:
        return s.scalar(select(UserSession.id).where(UserSession.token_hash == token_hash(token)))


def _view(row: PushSubscription, this_session: uuid.UUID | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "label": row.device_label,
        "platform": row.platform,
        "host": row.push_host,
        "preview_level": row.preview_level,
        "muted_until": row.muted_until.isoformat() if row.muted_until else None,
        "enabled": row.enabled and row.gone_at is None,
        "last_seen_at": row.last_seen_at.isoformat(),
        "this_device": this_session is not None and row.session_id == this_session,
    }


def make_router() -> APIRouter:
    router = APIRouter(
        prefix="/app/push",
        tags=["Owner: push devices"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text), Depends(require_push)],
    )

    def _mine(s: Any, person_id: uuid.UUID, user_id: uuid.UUID) -> Any:
        return select(PushSubscription).where(
            PushSubscription.agent_id == scope_or_default().agent_id,
            PushSubscription.person_id == person_id,
            PushSubscription.user_id == user_id,
        )

    @router.get("/key", summary="The VAPID public key", description="The applicationServerKey for subscribing.")
    def key(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        with session_scope() as s:
            keys = vapid.load(s)
            if keys is None:
                raise HTTPException(404, "Not Found")
            return {"public_key": keys.public_key}

    @router.put(
        "/subscription",
        summary="Subscribe this browser",
        description="Saves (or refreshes, on every app start) this browser's push subscription for the owner.",
    )
    def subscribe(
        body: SubscribeBody,
        request: Request,
        person_id: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        secret = Secret(
            endpoint=body.endpoint,
            p256dh=_b64url(body.keys.p256dh, 65, "The device key"),
            auth=_b64url(body.keys.auth, 16, "The device secret"),
        )
        this_session = _session_id(request)
        with session_scope() as s:
            try:
                row, created = upsert(
                    s, secret=secret, user_id=user_id, person_id=person_id, session_id=this_session,
                    agent_id=scope_or_default().agent_id, platform=body.platform, label=body.label.strip(),
                    default_preview=_default_preview(),
                )  # fmt: skip
            except BadSubscription as exc:
                raise HTTPException(422, str(exc)) from exc
            return {"created": created, "device": _view(row, this_session)}

    @router.delete("/subscription", summary="Unsubscribe this browser")
    def unsubscribe(
        body: EndpointBody,
        person_id: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, bool]:
        from superaide.features.push.subscriptions import endpoint_hash

        with session_scope() as s:
            row = s.scalar(
                _mine(s, person_id, user_id).where(PushSubscription.endpoint_hash == endpoint_hash(body.endpoint))
            )
            if row is None:
                raise HTTPException(404, "No such device.")
            remove(s, row, why="unsubscribed")
            return {"removed": True}

    @router.get("/devices", summary="My devices", description="Browsers and phones that get the owner's pushes.")
    def devices(
        request: Request, person_id: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> list[dict[str, Any]]:
        this_session = _session_id(request)
        with session_scope() as s:
            rows = s.scalars(_mine(s, person_id, user_id).order_by(PushSubscription.created_at)).all()
            return [_view(r, this_session) for r in rows]

    def _device(s: Any, device_id: uuid.UUID, person_id: uuid.UUID, user_id: uuid.UUID) -> PushSubscription:
        row = s.scalar(_mine(s, person_id, user_id).where(PushSubscription.id == device_id))
        if row is None:
            raise HTTPException(404, "No such device.")
        return row

    @router.patch("/devices/{device_id}", summary="Rename, change the preview, or mute a device")
    def update_device(
        device_id: uuid.UUID,
        body: DeviceBody,
        request: Request,
        person_id: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        with session_scope() as s:
            row = _device(s, device_id, person_id, user_id)
            if body.label is not None:
                row.device_label = body.label.strip()
            if body.preview_level is not None:
                row.preview_level = body.preview_level
            if body.mute_minutes is not None:
                row.muted_until = (
                    datetime.now(UTC) + timedelta(minutes=body.mute_minutes) if body.mute_minutes else None
                )
            s.flush()
            return _view(row, _session_id(request))

    @router.delete("/devices/{device_id}", summary="Remove a device")
    def delete_device(
        device_id: uuid.UUID,
        person_id: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, bool]:
        with session_scope() as s:
            remove(s, _device(s, device_id, person_id, user_id), why="removed")
            return {"removed": True}

    @router.post("/test", summary="Send a test push", description="'Ava can reach this phone', to every device.")
    def test_push(
        person_id: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        scope = scope_or_default()
        with session_scope() as s:
            rows = s.scalars(_mine(s, person_id, user_id)).all()
            if not rows:
                raise HTTPException(409, "Turn on notifications on a device first.")
            since = datetime.now(UTC) - timedelta(hours=1)
            recent = s.scalar(
                select(func.count(PushSend.id)).where(
                    PushSend.purpose == "test", PushSend.created_at >= since, PushSend.agent_id == scope.agent_id
                )
            )
            if (recent or 0) >= TESTS_PER_HOUR * len(rows):
                raise HTTPException(429, "That's enough tests for now. Try again in an hour.")
            item = payload.PushItem(kind="test", token=secrets.token_urlsafe(9))
            outcomes = send.send(s, item, rows, purpose="test", instance=str(scope.agent_id))
            return {"sent": sum(o.status == "sent" for o in outcomes), "devices": len(outcomes)}

    return router


def _default_preview() -> str:
    level = get_settings().push_default_preview
    return level if level in payload.LEVELS else "none"
