"""/app/tour (app.api): which guided tours this owner has finished or skipped (#626).

One row per (owner, tour), written only by the signed-in owner. The tour itself is front-end text; the server keeps
nothing but the answer, so a tour is shown once per account (not once per browser) and can be replayed. Owner only:
a stranger is refused by `owner_person_id` before anything is read, and Row-Level Security keeps other instances out.
No model call.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from superaide.app.deps import owner_person_id
from superaide.identity.auth.deps import reject_nul_text, require_csrf_header
from superaide.identity.auth.models import TourProgress
from superaide.platform.db.session import session_scope

# The tours the front end knows. "welcome" is the first-run prompt; the others are the per-page tours.
TOURS = ("welcome", "chat", "home", "connections", "notebook", "approvals", "settings")


class TourStatus(BaseModel):
    status: Literal["completed", "skipped"]


def make_tour_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/tour",
        tags=["Owner: guided tour"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "",
        summary="Which tours this owner has finished or skipped",
        description="`tours` maps a tour id to `completed` or `skipped`; a tour not listed has not been seen.",
    )
    def progress(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            rows = s.scalars(select(TourProgress).where(TourProgress.person_id == person_id)).all()
            return {"tours": {r.tour: r.status for r in rows}}

    @router.post(
        "/reset",
        summary="Replay: forget every tour answer",
        description="Deletes this owner's tour answers, so the tours show again.",
    )
    def reset(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, bool]:
        with session_scope() as s:
            s.execute(delete(TourProgress).where(TourProgress.person_id == person_id))
        return {"ok": True}

    @router.post(
        "/{tour}",
        summary="Record that a tour was finished or skipped",
        description="Idempotent. `tour` must be one of the known tour ids.",
    )
    def record(tour: str, body: TourStatus, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        if tour not in TOURS:
            raise HTTPException(404, "Not Found")
        with session_scope() as s:
            row = s.scalars(
                select(TourProgress).where(TourProgress.person_id == person_id, TourProgress.tour == tour)
            ).first()
            if row is None:
                s.add(TourProgress(person_id=person_id, tour=tour, status=body.status))
            else:
                row.status = body.status
                row.updated_at = datetime.now(UTC)
        return {"tour": tour, "status": body.status}

    return router
