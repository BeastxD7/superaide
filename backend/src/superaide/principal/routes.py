"""HTTP side of the Principal Card (#135).

Public `GET /p/card/{token}`: anonymous; 404 for a forged, expired or revoked card and while the flag is off
(all the same body). It reads only the card and one generation number.
Owner `/app/principal-card`: view and set the display name and lists, and revoke every card at once.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from superaide.app.routes import owner_person_id
from superaide.audit import audit
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.config import get_settings
from superaide.db.session import session_scope
from superaide.principal import card
from superaide.tenancy.context import use_instance


def _not_found() -> HTTPException:
    return HTTPException(404, "Not Found")


def flag_on() -> None:
    if not card.enabled():
        raise _not_found()


class CardBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str = Field(min_length=1, max_length=card.MAX_NAME)
    can: list[str] | None = Field(default=None, max_length=card.MAX_LISTED)
    cannot: list[str] | None = Field(default=None, max_length=card.MAX_LISTED)


def make_public_router() -> APIRouter:
    router = APIRouter(prefix="/p/card", tags=["Public: Principal card"])

    @router.get(
        "/{token}",
        summary="A principal card",
        description="The signed statement that a message came from an AI acting for someone. Nothing private.",
        dependencies=[Depends(flag_on)],
    )
    def page(token: str) -> dict[str, Any]:
        scope = card.scope_of(token)
        if scope is None:
            raise _not_found()
        with use_instance(scope), session_scope() as s:
            shown = card.verify(s, token)
        if shown is None:
            raise _not_found()
        return shown

    return router


def make_owner_router() -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        """The owner, once the card is known to be on: others get 401/403 first, as everywhere."""
        if not card.enabled():
            raise _not_found()
        return person_id

    def _view(s: Any, cur: dict[str, Any]) -> dict[str, Any]:
        token = card.issue(s)
        return {k: cur[k] for k in ("display_name", "can", "cannot")} | {
            "active": bool(cur["display_name"]),
            "preview": f"{get_settings().frontend_origin.rstrip('/')}/c/{token}" if token else None,
        }

    router = APIRouter(
        prefix="/app/principal-card",
        tags=["Owner: principal card"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="My principal card settings")
    def get_card(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            cur = card.settings_of(s)
            return _view(s, cur)

    @router.put(
        "",
        summary="Set my principal card",
        description="The display name is what anyone who opens a card sees; choose it yourself.",
    )
    def put_card(body: CardBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            cur = card.configure(s, display_name=body.display_name, can=body.can, cannot=body.cannot)
            audit(s, "owner", "principal_card_set")
            return _view(s, cur)

    @router.post(
        "/revoke",
        summary="Revoke every card I have sent",
    )
    def revoke(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            gen = card.revoke_all(s)
            audit(s, "owner", "principal_card_revoked", generation=gen)
            return {"revoked": True}

    return router
