"""/app/skill-receipts: the receipt log, computed tiers, the export and a bucketed claim (issue #131).

Behind SKILL_RECEIPTS_ENABLED (404 while off, after strangers are refused). Owner only, CSRF-checked. The export is
the owner's to hand out: making a log public is the owner's decision, so nothing here publishes it. No model call."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.routes import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.db.session import session_scope
from superaide.skills import claim, receipts


class RevertBody(BaseModel):
    seq: int = Field(ge=1)


class ClaimBody(BaseModel):
    counterparty: str = Field(min_length=1, max_length=200)


def receipts_owner(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
    from superaide.config import get_settings

    if not get_settings().skill_receipts_enabled:
        raise HTTPException(404, "Not Found")
    return person_id


def make_skill_receipts_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/skill-receipts",
        tags=["Owner: skills"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Chain status and computed tiers", description="404 while SKILL_RECEIPTS_ENABLED is off.")
    def overview(person_id: uuid.UUID = Depends(receipts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            data = receipts.rows(s)
            return {"count": len(data), "chain": receipts.verify(s), "tiers": receipts.tiers(s)}

    @router.get("/export", summary="The receipts, the rules and a Merkle root per day")
    def export(person_id: uuid.UUID = Depends(receipts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            return receipts.export(s)

    @router.post("/revert", summary="Record that an approved action was undone")
    def revert(body: RevertBody, person_id: uuid.UUID = Depends(receipts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                receipts.record_revert(s, body.seq)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            return {"chain": receipts.verify(s), "tiers": receipts.tiers(s)}

    @router.post("/claim", summary="A signed, bucketed, 24-hour trust claim for one counterparty")
    def make_claim(body: ClaimBody, person_id: uuid.UUID = Depends(receipts_owner)) -> dict[str, Any]:
        with session_scope() as s:
            token = claim.issue(s, body.counterparty)
        if token is None:
            raise HTTPException(409, "Not enough decisions in the log to make a claim yet")
        return {"token": token, "claim": claim.verify(token)}

    return router
