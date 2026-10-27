"""Owner routes for mandates (#112), under `/app/mandates`. 404 for the owner while MANDATES_ENABLED is off; others
get 401/403 first. The card text always comes from the token's fields, never from a model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.mandate import service
from superaide.mandate import token as tk
from superaide.mandate.models import Mandate
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope


class Narrowing(BaseModel):
    """Only restrictions. Each is optional; a delegate must name at least one."""

    model_config = ConfigDict(extra="forbid")

    tools: list[str] | None = Field(default=None, max_length=20)
    expires: datetime | None = None
    max_uses: int | None = Field(default=None, ge=0, le=100000)
    max_amount: float | None = Field(default=None, ge=0)
    recipient_domains: list[str] | None = Field(default=None, max_length=20)
    topics: list[str] | None = Field(default=None, max_length=20)

    def limits(self) -> dict[str, Any]:
        out = {k: v for k, v in self.model_dump(include=set(Narrowing.model_fields)).items() if v is not None}
        if "expires" in out:
            out["expires"] = out["expires"].astimezone(UTC).isoformat() if out["expires"].tzinfo else None
        return out


class CreateBody(Narrowing):
    label: str = Field(min_length=1, max_length=120)
    tools: list[str] = Field(min_length=1, max_length=20)
    expires: datetime


class DelegateBody(Narrowing):
    label: str = Field(min_length=1, max_length=120)


def make_router() -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_settings().mandates_enabled:
            raise HTTPException(404, "Not Found")
        return person_id

    router = APIRouter(
        prefix="/app/mandates",
        tags=["Owner: mandates"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="My mandates, each with the chain it was handed down through")
    def listing(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            rows = s.scalars(select(Mandate).order_by(Mandate.created_at)).all()
            return {
                "mandates": [service.chain_view(s, r) for r in rows],
                "skipping": service.skip_enabled(),
                "skips": service.recent_skips(s),
            }

    @router.post("", summary="Sign a mandate card", description="A named, limited thing Ava may do without asking.")
    def create(body: CreateBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        if body.expires.tzinfo is None:
            raise HTTPException(422, "Give the expiry with a time zone.")
        limits = {k: v for k, v in body.limits().items() if k not in {"tools", "expires"}}
        with session_scope() as s:
            try:
                row = service.create(s, person_id, tools=body.tools, expires=body.expires, label=body.label, **limits)
            except tk.MandateError as exc:
                raise HTTPException(422, str(exc)) from exc
            return service.chain_view(s, row)

    @router.post("/{mandate_id}/delegate", summary="Hand a mandate on, narrower")
    def delegate(mandate_id: uuid.UUID, body: DelegateBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        if body.expires is not None and body.expires.tzinfo is None:
            raise HTTPException(422, "Give the expiry with a time zone.")
        with session_scope() as s:
            try:
                row = service.delegate(s, mandate_id, label=body.label, **body.limits())
            except tk.MandateError as exc:
                raise HTTPException(422, str(exc)) from exc  # a widening is refused here, with the reason
            return service.chain_view(s, row)

    @router.post("/{mandate_id}/revoke", summary="Revoke a mandate and everything handed on from it")
    def revoke(mandate_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            if s.get(Mandate, mandate_id) is None:
                raise HTTPException(404, "No such mandate")
            service.revoke(s, mandate_id)
            return {"revoked": True}

    return router
