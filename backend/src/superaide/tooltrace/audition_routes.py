"""Audition (issue #110): owner-only routes, 404 while the feature's flag is off.

Sign-in, ownership and CSRF are checked first (as on every owner route), so only the owner learns it is off.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from superaide.app.routes import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.config import get_settings
from superaide.db.models import Person
from superaide.db.session import require, session_scope


class AuditionBody(BaseModel):
    skills: list[str] = Field(min_length=2, max_length=3)
    tasks: int | None = Field(default=None, ge=1, le=50)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app",
        tags=["Owner: skills"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def audition_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_settings().audition_enabled:
            raise HTTPException(404, "Not found")
        return person_id

    @router.get(
        "/audition/candidates",
        tags=["Owner: skills"],
        summary="Skills you can audition",
        description="Audition (AUDITION_ENABLED, off by default; 404 while off): the skills that can be compared.",
    )
    def audition_candidates(person_id: uuid.UUID = Depends(audition_on)) -> list[dict[str, Any]]:
        from superaide.tooltrace import audition

        return audition.candidates()

    @router.post(
        "/audition",
        tags=["Owner: skills"],
        summary="Audition 2 or 3 skills",
        description=(
            "Runs your last tasks (at most AUDITION_MAX_TASKS) with each candidate skill added, effects stubbed and "
            "no network, and shows side by side which tools each would have called (names and short hashes only)."
        ),
    )
    def audition_run(body: AuditionBody, person_id: uuid.UUID = Depends(audition_on)) -> dict[str, Any]:
        from superaide.tooltrace import audition
        from superaide.tooltrace.rehearse import RehearsalError

        agent = get_agent()
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return audition.run(agent, s, owner, body.skills, body.tasks)
            except RehearsalError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    return router
