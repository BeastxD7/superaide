"""/app/journal: "Ava keeps score", the owner's decision journal and calibration (Notebook > Journal).

Owner only, CSRF-checked, per instance, off until the owner turns it on. Predictions are the owner's own and
private. Turning it off deletes them. Scoring is plain arithmetic in code (journal/score.py)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete

from superaide.app.routes import owner_person_id
from superaide.audit import audit
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.db.models import Prediction
from superaide.db.session import rows_affected, session_scope
from superaide.journal import service


class SettingsBody(BaseModel):
    enabled: bool


class PredictionBody(BaseModel):
    statement: str = Field(min_length=3, max_length=500)
    confidence_pct: int = Field(ge=50, le=99)
    due_on: date | None = None
    reason: str = Field(default="", max_length=500)
    topic: str = Field(default="other", max_length=20)


class ResolveBody(BaseModel):
    came_true: bool
    note: str = Field(default="", max_length=500)


def make_journal_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/journal",
        tags=["Owner: decision journal"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def need_on(s: Any, person_id: uuid.UUID) -> None:
        if not service.is_on(s, person_id):
            raise HTTPException(409, "Turn the decision journal on first.")

    @router.get(
        "",
        summary="The decision journal",
        description="Whether it's on, the owner's predictions, and the calibration once 10 have been resolved.",
    )
    def journal(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return service.page(s, person_id, datetime.now(UTC).date())

    @router.post(
        "/settings",
        summary="Turn the journal on or off",
        description="Off deletes every prediction in the journal.",
    )
    def settings(body: SettingsBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            deleted = service.set_enabled(s, person_id, body.enabled)
            return {"enabled": body.enabled, "deleted": deleted}

    @router.post("/predictions", summary="Record a prediction", description="In the owner's own words.")
    def add_prediction(body: PredictionBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            need_on(s, person_id)
            p = service.add(
                s,
                person_id,
                statement=body.statement,
                confidence_pct=body.confidence_pct,
                due_on=body.due_on,
                reason=body.reason,
                topic=body.topic,
                quote=body.statement,
            )
            if p is None:
                raise HTTPException(409, "That one is already in your journal.")
            return service.view(p, datetime.now(UTC).date())

    @router.post("/predictions/{prediction_id}/resolve", summary="Say how it turned out")
    def resolve(
        prediction_id: uuid.UUID, body: ResolveBody, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            need_on(s, person_id)
            p = s.get(Prediction, prediction_id)
            if p is None or p.owner_person_id != person_id or p.status != "open":
                raise HTTPException(404, "Not found in your journal")
            service.resolve(s, p, came_true=body.came_true, note=body.note)
            return service.view(p, datetime.now(UTC).date())

    @router.post("/predictions/{prediction_id}/erase", summary="Erase a prediction")
    def erase(prediction_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        with session_scope() as s:
            gone = rows_affected(
                s.execute(
                    delete(Prediction).where(Prediction.id == prediction_id, Prediction.owner_person_id == person_id)
                )
            )
            if not gone:
                raise HTTPException(404, "Not found in your journal")
            audit(s, "journal", "prediction_erased", prediction_id=prediction_id, person_id=person_id)
            return {"status": "erased"}

    return router
