"""POST /app/stepup: ask for the single-use confirmation a sensitive change needs (issue #451)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from superaide.app.deps import owner_person_id
from superaide.auth import stepup
from superaide.auth.routes import current_user_id, reject_nul_text, require_csrf_header, session_token
from superaide.platform.db.models import UserAccount
from superaide.platform.db.session import session_scope


class StepUpBody(BaseModel):
    action: str


def make_router() -> APIRouter:
    router = APIRouter(
        prefix="/app/stepup",
        tags=["Owner: confirmation"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.post(
        "",
        summary="Ask to confirm a sensitive change",
        description=(
            "Returns a single-use `nonce` (valid two minutes, tied to this session and to `action`) and the `method` "
            "to show: `confirm` (a dialog with 'I understand') or `password` (STEPUP_MODE=password, for accounts "
            "that have one). Send the nonce as `confirmation` with the change. Refusals are 403."
        ),
    )
    def ask(
        request: Request,
        body: StepUpBody,
        _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        with session_scope() as s:
            user = s.get(UserAccount, user_id)
            if user is None:
                raise HTTPException(403, "Confirm in a signed-in session.")
            try:
                out = stepup.issue(s, user=user, session_token=session_token(request), action=body.action)
            except stepup.StepUpError as exc:
                raise HTTPException(403, str(exc)) from exc
            out["what"] = stepup.ACTIONS[body.action]
            return out

    return router
