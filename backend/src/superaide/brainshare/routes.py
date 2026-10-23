"""HTTP side of the Glass brain share link (#55).

Public `GET /p/brain/{token}` (counts as JSON) and `GET /p/brain/{token}/picture.svg`: anonymous; the same 404
for an unknown, forged, expired or revoked token and while the flag is off. They show counts only.
Owner `/app/brain/share-links`: create (the token is shown once), list, revoke. 404 while the flag is off.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update

from superaide.app.brain_map import share_counts, share_svg
from superaide.app.routes import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.brainshare import service
from superaide.brainshare.models import BrainShare
from superaide.db.session import session_scope
from superaide.tenancy.context import all_instances, use_instance

PRIVATE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


def _not_found() -> HTTPException:
    return HTTPException(404, "Not Found")


def flag_on() -> None:
    if not service.enabled():
        raise _not_found()


class ShareBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    days: int = Field(default=1, ge=1, le=service.MAX_DAYS)


def make_public_router() -> APIRouter:
    router = APIRouter(prefix="/p/brain", tags=["Public: Glass brain share"], dependencies=[Depends(flag_on)])

    def open_link(token: str) -> tuple[uuid.UUID, Any, Any]:
        with all_instances(), session_scope() as s:
            row = service.find(s, token)
            if row is None:
                raise _not_found()
            share_id, scope, owner_id = row.id, service.scope_of(row), row.owner_person_id
        if not service.within_limit(share_id):
            raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
        return share_id, scope, owner_id

    def seen(session: Any, share_id: uuid.UUID) -> None:
        from datetime import UTC, datetime

        session.execute(
            update(BrainShare)
            .where(BrainShare.id == share_id)
            .values(views=BrainShare.views + 1, last_viewed_at=datetime.now(UTC))
        )

    @router.get(
        "/{token}",
        summary="The shape of an assistant's memory, in numbers",
        description="Counts per topic and per origin. No names, no note text. 404 for anything but a live link.",
    )
    def counts(token: str, response: Response) -> dict[str, Any]:
        share_id, scope, owner_id = open_link(token)
        with use_instance(scope), session_scope() as s:
            out = share_counts(s, owner_id)
            expires = s.get(BrainShare, share_id)
            seen(s, share_id)
            out["expires_at"] = expires.expires_at.isoformat() if expires else None
        response.headers.update(PRIVATE)
        return out

    @router.get(
        "/{token}/picture.svg",
        summary="The picture of the shape of the memory",
        description="The same picture the owner can download: counts and colours only.",
        response_class=Response,
    )
    def picture(token: str) -> Response:
        share_id, scope, owner_id = open_link(token)
        with use_instance(scope), session_scope() as s:
            svg = share_svg(s, owner_id)
            seen(s, share_id)
        return Response(svg, media_type="image/svg+xml", headers=PRIVATE)

    return router


def make_owner_router() -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        """The owner, once the feature is known to be on: others get 401/403 first, as everywhere."""
        if not service.enabled():
            raise _not_found()
        return person_id

    router = APIRouter(
        prefix="/app/brain/share-links",
        tags=["Owner: glass brain share"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="My share links", description="404 while BRAIN_SHARE_ENABLED is off.")
    def overview(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return {
                "links": [service.view(r) for r in service.links(s, person_id)],
                "max_days": service.MAX_DAYS,
                "max_active": service.MAX_ACTIVE,
            }

    @router.post(
        "",
        summary="Create an unlisted link to the counts-only picture",
        description="The token is shown once and only its hash is kept. Expires in 1 to 7 days.",
    )
    def create(body: ShareBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                row, token = service.create(s, person_id, body.days)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return service.view(row) | {"token": token, "path": f"/s/{token}"}

    @router.post("/{share_id}/revoke", summary="Stop a link working", description="Takes effect at once.")
    def revoke(share_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, bool]:
        with session_scope() as s:
            if not service.revoke(s, person_id, share_id):
                raise HTTPException(404, "No such link")
            return {"revoked": True}

    return router
