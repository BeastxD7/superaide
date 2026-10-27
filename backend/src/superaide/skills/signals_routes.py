"""/app/skill-signals: the disagreement ledger, generated behaviour cards and demotions (issue #130).

Behind SKILL_SIGNALS_ENABLED (404 while off, after strangers are refused). Owner only, CSRF-checked. No model call."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.platform.db.session import session_scope
from superaide.skills import registry, signals


class ScanBody(BaseModel):
    skill_id: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$", max_length=120)


class LabelBody(ScanBody):
    version: str = Field(min_length=1, max_length=40)
    scanner: str = Field(min_length=1, max_length=40)
    concern: bool


class ClearBody(ScanBody):
    version: str = Field(min_length=1, max_length=40)


def signals_owner(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
    from superaide.platform.config import get_settings

    if not get_settings().skill_signals_enabled:
        raise HTTPException(404, "Not Found")
    return person_id


def make_skill_signals_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/skill-signals",
        tags=["Owner: skills"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Scanner ledger, behaviour cards and demotions", description="404 while off.")
    def overview(person_id: uuid.UUID = Depends(signals_owner)) -> dict[str, Any]:
        with session_scope() as s:
            return signals.view(s)

    @router.post("/scan", summary="Run every scanner on an installed catalog skill")
    def scan(body: ScanBody, person_id: uuid.UUID = Depends(signals_owner)) -> dict[str, Any]:
        skill = registry.catalog().get(body.skill_id)
        if skill is None:
            raise HTTPException(404, "No such capability")
        with session_scope() as s:
            signals.scan_skill(s, skill)
            return signals.view(s)

    @router.post("/label", summary="Label a scanner verdict as a real concern or not")
    def label(body: LabelBody, person_id: uuid.UUID = Depends(signals_owner)) -> dict[str, Any]:
        with session_scope() as s:
            signals.label_concern(s, body.skill_id, body.version, body.scanner, body.concern)
            return signals.view(s)

    @router.post("/clear-demotion", summary="Put a demoted skill version back after reviewing it")
    def clear(body: ClearBody, person_id: uuid.UUID = Depends(signals_owner)) -> dict[str, Any]:
        with session_scope() as s:
            if not signals.clear_demotion(s, body.skill_id, body.version):
                raise HTTPException(404, "That version isn't demoted")
            return signals.view(s)

    return router
