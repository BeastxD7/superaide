"""Replay before update (issue #110): owner-only routes, 404 while the feature's flag is off.

Sign-in, ownership and CSRF are checked first (as on every owner route), so only the owner learns it is off.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.platform.config import get_settings
from superaide.platform.db.models import Person
from superaide.platform.db.session import require, session_scope


class PreviewBody(BaseModel):
    kind: str = Field(max_length=10)
    skill_id: str = Field(default="", max_length=80)
    model: str = Field(default="", max_length=120)
    prompt: str = Field(default="", max_length=4000)
    tasks: int | None = Field(default=None, ge=1, le=50)


class ApprovePreviewBody(BaseModel):
    accepted: list[str] = Field(default_factory=list, max_length=20)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app",
        tags=["Owner: skills"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def updates_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_settings().replay_before_update_enabled:
            raise HTTPException(404, "Not found")
        return person_id

    def _preview_view(p: Any) -> dict[str, Any]:
        from superaide.tooltrace.preview import headline

        return {
            "id": str(p.id), "kind": p.kind, "label": p.label, "status": p.status, "headline": headline(p),
            "tasks_asked": p.tasks_asked, "tasks_done": p.tasks_done, "changed": p.changed, "stopped": p.stopped,
            "tasks": p.summary, "at": p.created_at.isoformat(),
        }  # fmt: skip

    @router.get(
        "/updates",
        tags=["Owner: skills"],
        summary="Update previews",
        description=(
            "Replay before update (REPLAY_BEFORE_UPDATE_ENABLED, off by default; 404 while off): what a skill, prompt "
            "or model change would have done differently on your recent tasks, as tool names and short hashes only."
        ),
    )
    def updates_list(person_id: uuid.UUID = Depends(updates_on)) -> list[dict[str, Any]]:
        from superaide.tooltrace.models import UpdatePreview

        with session_scope() as s:
            rows = s.scalars(
                select(UpdatePreview)
                .where(UpdatePreview.owner_person_id == person_id)
                .order_by(UpdatePreview.created_at.desc())
                .limit(30)
            ).all()
            return [_preview_view(p) for p in rows]

    @router.post(
        "/updates/preview",
        tags=["Owner: skills"],
        summary="Preview an update",
        description=(
            "Replays your last tasks (at most REPLAY_MAX_TASKS) with the current setup and with the change, effects "
            "stubbed and no network, and reports how the calls differ. Nothing is changed."
        ),
    )
    def updates_preview(body: PreviewBody, person_id: uuid.UUID = Depends(updates_on)) -> dict[str, Any]:
        from superaide.tooltrace import preview
        from superaide.tooltrace.rehearse import RehearsalError

        agent = get_agent()
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                row = preview.create(
                    agent, s, owner, kind=body.kind, skill_id=body.skill_id, model=body.model, prompt=body.prompt,
                    tasks=body.tasks,
                )  # fmt: skip
            except RehearsalError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return _preview_view(row)

    @router.post(
        "/updates/{preview_id}/approve",
        tags=["Owner: skills"],
        summary="Approve an update",
        description="Approving a skill installs it (send the permissions you agreed to); other changes are recorded.",
    )
    def updates_approve(
        preview_id: uuid.UUID, body: ApprovePreviewBody, person_id: uuid.UUID = Depends(updates_on)
    ) -> dict[str, Any]:
        from superaide.tooltrace import preview
        from superaide.tooltrace.rehearse import RehearsalError

        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                row, applied = preview.approve(s, owner, preview_id, body.accepted)
            except RehearsalError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return {**_preview_view(row), "applied": applied}

    @router.post("/updates/{preview_id}/reject", tags=["Owner: skills"], summary="Reject an update")
    def updates_reject(preview_id: uuid.UUID, person_id: uuid.UUID = Depends(updates_on)) -> dict[str, Any]:
        from superaide.tooltrace import preview
        from superaide.tooltrace.rehearse import RehearsalError

        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return _preview_view(preview.reject(s, owner, preview_id))
            except RehearsalError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    return router
