"""HTTP side of the Principal Card (#135).

Public `GET /p/card/{token}`: anonymous; 404 for a forged, expired or revoked card and while the flag is off
(all the same body). It reads only the card and one generation number.
Owner `/app/principal-card`: view and set the display name and lists, and revoke every card at once.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from superaide.app.deps import owner_person_id
from superaide.auth.routes import client_of, reject_nul_text, require_csrf_header
from superaide.links import is_local, public_base
from superaide.platform.audit import audit
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import use_instance
from superaide.principal import card


def _not_found() -> HTTPException:
    return HTTPException(404, "Not Found")


def flag_on() -> None:
    if not card.enabled():
        raise _not_found()


class CardBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    first_name: str = Field(default="", max_length=card.MAX_FIRST)  # blank: use the first name on the account
    initial: str = Field(default="", max_length=2)
    sign_default: bool | None = None  # omitted keeps the owner's choice (the default is on)
    channels: bool | None = None  # also sign Telegram and Slack replies to other people (default off)
    wording: str | None = Field(default=None, max_length=card.MAX_WORDING)
    can: list[str] | None = Field(default=None, max_length=card.MAX_LISTED)
    cannot: list[str] | None = Field(default=None, max_length=card.MAX_LISTED)


class SignDefault(BaseModel):
    model_config = ConfigDict(extra="ignore")

    on: bool


def make_public_router() -> APIRouter:
    router = APIRouter(prefix="/p/card", tags=["Public: Principal card"])

    @router.get(
        "/{token}",
        summary="A principal card",
        description="The signed statement that a message came from an AI acting for someone. Nothing private.",
        dependencies=[Depends(flag_on)],
    )
    def page(token: str, request: Request) -> dict[str, Any]:
        ip = client_of(request).ip
        with session_scope() as s:
            if card.blocked(s, ip):  # too many misses from this address in the last minute
                raise HTTPException(429, "Too many tries. Wait a minute and try again.", headers={"Retry-After": "60"})
        scope = card.scope_of(token)
        shown = None
        if scope is not None:
            with use_instance(scope), session_scope() as s:
                shown = card.verify(s, token, record=True)
        if shown is None:
            with session_scope() as s:
                card.record_miss(s, ip)
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
        link = card.issue_link(s)
        keys = ("first_name", "initial", "display_name", "can", "cannot", "sign_default", "channels", "wording")
        return {k: cur[k] for k in keys} | {
            "name_shown": card.owner_name(s),
            "default_wording": card.DEFAULT_WORDING,
            "active": cur["sign_default"] and bool(card.owner_name(s)),
            "preview": link["url"] if link else None,
            "address": public_base(),
            "address_is_local": is_local(public_base()),
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
            try:
                cur = card.configure(
                    s, first_name=body.first_name, initial=body.initial, can=body.can, cannot=body.cannot,
                    sign_default=body.sign_default, channels=body.channels, wording=body.wording,
                )  # fmt: skip
            except ValueError:
                raise HTTPException(422, "first_name must be a single first name (letters only)") from None
            audit(s, "owner", "principal_card_set", sign_default=cur["sign_default"])
            return _view(s, cur)

    @router.post(
        "/sign-default",
        summary="Sign my emails by default, or not",
        description="On by default. Off keeps every setting; each email's approval card still has its own tick.",
    )
    def sign_default(body: SignDefault, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            cur = card.set_sign_default(s, body.on)
            audit(s, "owner", "principal_card_sign_on" if body.on else "principal_card_sign_off")
            return _view(s, cur)

    @router.get(
        "/stats",
        summary="How my cards are doing",
        description="Counts only: cards shown, distinct cards opened, cards revoked. No recipients, no text.",
    )
    def stats(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return card.stats(s)

    @router.post(
        "/revoke",
        summary="Revoke every card I have sent",
    )
    def revoke(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            gen = card.revoke_all(s)
            audit(s, "owner", "principal_card_revoked", generation=gen)
            return {"revoked": True}

    @router.get(
        "/sent",
        summary="Cards I have sent",
        description="Newest first: channel, who it went to, when, and whether it is still valid.",
    )
    def sent(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return {"cards": card.sent(s)}

    @router.post("/sent/{card_id}/revoke", summary="Revoke one card I have sent")
    def revoke_one(card_id: str = Path(max_length=40), person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            if not card.revoke_one(s, card_id):
                raise _not_found()
            audit(s, "owner", "principal_card_revoked_one", card_id=card_id)
            return {"revoked": True}

    return router
