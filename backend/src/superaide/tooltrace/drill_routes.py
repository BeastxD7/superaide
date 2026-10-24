"""Model-swap drill (issue #110): owner-only routes, 404 while the feature's flag is off.

Sign-in, ownership and CSRF are checked first (as on every owner route), so only the owner learns it is off.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.deps import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.config import get_settings
from superaide.db.models import Person
from superaide.db.session import require, session_scope


class DrillBody(BaseModel):
    model_a: str = Field(default="", max_length=120)  # empty: the model Ava uses now
    model_b: str = Field(max_length=120)
    tasks: int | None = Field(default=None, ge=1, le=50)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app",
        tags=["Owner: skills"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def drill_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_settings().model_drill_enabled:
            raise HTTPException(404, "Not found")
        return person_id

    @router.get(
        "/model-drill",
        tags=["Owner: skills"],
        summary="Model-swap drill",
        description=(
            "MODEL_DRILL_ENABLED (off by default; 404 while off): the model Ava uses now and how many recent tasks "
            "a drill can replay."
        ),
    )
    def drill_info(person_id: uuid.UUID = Depends(drill_on)) -> dict[str, Any]:
        from superaide.tooltrace.rehearse import recent_tasks

        settings = get_settings()
        with session_scope() as s:
            owner = require(s, Person, person_id)
            return {
                "current_model": settings.model_chat,
                "max_tasks": settings.drill_max_tasks,
                "tasks_available": len(recent_tasks(s, owner, settings.drill_max_tasks)),
            }

    @router.post(
        "/model-drill",
        tags=["Owner: skills"],
        summary="Replay on two models",
        description=(
            "Replays your last tasks (at most DRILL_MAX_TASKS) on each model with effects stubbed and no network, and "
            "reports where the call sequences differ (tool names and short argument hashes only)."
        ),
    )
    def drill_run(body: DrillBody, person_id: uuid.UUID = Depends(drill_on)) -> dict[str, Any]:
        from superaide.tooltrace import drill
        from superaide.tooltrace.rehearse import RehearsalError

        agent = get_agent()
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return drill.run(agent, s, owner, body.model_a, body.model_b, body.tasks)
            except RehearsalError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    return router
