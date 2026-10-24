"""Owner routes for Fork Night (#127): queue a question for tonight, read the morning card, pick a draft.
404 for the owner while it is off; others get 401/403 first."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.db.models import Person
from superaide.db.session import session_scope
from superaide.forknight import night


class QueueBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str = Field(min_length=3, max_length=2000)


class PickBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lens: str = Field(min_length=1, max_length=20)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not night.enabled(get_agent().settings):
            raise HTTPException(404, "Not Found")
        return person_id

    router = APIRouter(
        prefix="/app/fork-night",
        tags=["Owner: fork night"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Last nights' three drafts")
    def runs(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return {"runs": night.listing(s)}

    @router.post("", summary="Leave a choice for tonight", description="Three read-only drafts, ready in the morning.")
    def queue(body: QueueBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            owner = s.scalar(select(Person).where(Person.id == person_id))
            assert owner is not None
            return night.queue(s, owner, get_agent().settings, body.question)

    @router.post("/{run_id}/pick", summary="Pick one of the three drafts")
    def pick(run_id: str, body: PickBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            if not night.pick(s, run_id, body.lens):
                raise HTTPException(404, "No such draft")
            return {"picked": body.lens}

    return router
