"""The owner's side of "someone is named like a contact" (issue #93): list the questions, answer one.

Owner only, behind IDENTITY_CLAIM_REVIEW (404 while off, after sign-in and CSRF so others get 401/403).
The answer is the only thing that links an account; it comes from the signed-in owner, never from a chat."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from superaide.app.deps import owner_person_id
from superaide.identity import claims
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.identity.models import Person
from superaide.platform.db.session import require, session_scope


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: Literal["contact", "new", "stranger", "block"]
    relation: str = "contact"  # how the owner knows them, for "someone new"


def make_router() -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not claims.enabled():
            raise HTTPException(404, "Not Found")
        return person_id

    router = APIRouter(
        prefix="/app",
        tags=["Owner: identity claims"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "/identity-claims",
        summary="Who is this? Accounts named like someone you know",
        description="Open questions with everything the platform told us. Nothing is linked until you answer.",
    )
    def list_claims(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return {"claims": [claims.view(s, c) for c in claims.open_claims(s)], "relations": list(claims.RELATIONS)}

    @router.post(
        "/identity-claims/{claim_id}/answer",
        summary="Answer: link, add as a person, keep as a stranger, or block",
        description="Only this links the account. Each question is answered once.",
    )
    def answer_claim(claim_id: uuid.UUID, body: AnswerBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            row = claims.get(s, claim_id)
            if row is None:
                raise HTTPException(404, "Not found")
            try:
                claims.answer(s, row, choice=body.choice, decided_by=owner, relation=body.relation)
            except claims.ClaimError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return {"status": row.status}

    return router
