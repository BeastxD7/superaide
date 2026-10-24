"""/app/mirror: "The Mirror", a weekly note on patterns with receipts (Notebook > The Mirror).

Behind MIRROR_ENABLED (off by default: 404 while off, after the owner checks) and an opt-in per owner.
Owner only, CSRF-checked. No model call."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from superaide.app.deps import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.features.mirror import service
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope


class SettingsBody(BaseModel):
    enabled: bool


class FeedbackBody(BaseModel):
    action: Literal["not_useful", "mute"]


def mirror_owner(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
    """The owner, once the feature is on for this server. Strangers and signed-out visitors are refused first."""
    if not get_settings().mirror_enabled:
        raise HTTPException(404, "Not Found")
    return person_id


def make_mirror_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/mirror",
        tags=["Owner: the Mirror"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="This week's note", description="404 while MIRROR_ENABLED is off.")
    def mirror(person_id: uuid.UUID = Depends(mirror_owner)) -> dict[str, Any]:
        with session_scope() as s:
            return service.view(s, person_id)

    @router.post("/settings", summary="Turn the Mirror on or off", description="Off deletes every note.")
    def settings(body: SettingsBody, person_id: uuid.UUID = Depends(mirror_owner)) -> dict[str, Any]:
        with session_scope() as s:
            deleted = service.set_enabled(s, person_id, body.enabled)
            return {"enabled": body.enabled, "deleted": deleted}

    @router.post(
        "/notes/{note_id}/findings/{index}/feedback",
        summary="Not useful, or don't mention again",
        description="`mute` stores the pattern so it never comes back; `not_useful` counts against that kind.",
    )
    def feedback(
        note_id: uuid.UUID, index: int, body: FeedbackBody, person_id: uuid.UUID = Depends(mirror_owner)
    ) -> dict[str, str]:
        with session_scope() as s:
            if not service.give_feedback(s, person_id, note_id, index, body.action):
                raise HTTPException(404, "Not found")
            return {"status": body.action}

    return router
