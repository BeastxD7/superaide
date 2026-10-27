"""Owner route for the Dissent Desk (#126): `POST /app/dissent` asks three advisers about one choice.
404 for the owner while the desk is off or fewer than three models are configured; others get 401/403 first."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from superaide.app.deps import owner_person_id
from superaide.features.dissent import desk
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.platform.db.session import session_scope


class AskBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str = Field(min_length=3, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=4)
    kind: str = Field(default="", max_length=40)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not desk.ready(get_agent().settings):
            raise HTTPException(404, "Not Found")
        return person_id

    router = APIRouter(
        prefix="/app/dissent",
        tags=["Owner: dissent desk"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.post(
        "",
        summary="Ask three advisers about a high-stakes choice",
        description="Three different models answer on their own; plain code decides whether a minority view is shown.",
    )
    def ask(body: AskBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        agent = get_agent()
        with session_scope() as s:
            verdict = desk.ask(agent, s, body.question, body.options, kind=body.kind)
        if verdict is None:
            return {"ran": False, "reason": "Low stakes, or the advisers could not answer within the budget."}
        return {
            "ran": True,
            "majority": verdict.majority,
            "dissent": None
            if verdict.dissent is None
            else {"choice": verdict.dissent.choice, "reason": verdict.dissent.reason},
            "lines": verdict.lines(),
        }

    return router
