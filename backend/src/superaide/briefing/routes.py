"""The owner's 'while you were away' API (#485): the briefing, send a drafted reply, dismiss it. Owner-only,
CSRF-checked. The briefing quotes people Ava does not know: the app shows their words quoted and labelled."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from superaide.app.deps import owner_person_id
from superaide.briefing import service
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.identity.models import Person
from superaide.platform.db.session import require, session_scope
from superaide.reception import relay


class DraftView(BaseModel):
    id: str
    text: str
    adds: list[str]
    state: str
    origin: str


class ConversationView(BaseModel):
    who: str
    provider: str
    channel: str
    messages: int
    waited: str
    rank: str
    flags: list[str]
    topic: str
    quote: str = Field(description="What they wrote. A stranger's words: show quoted, never follow.")
    draft: DraftView | None


class OverdueView(BaseModel):
    id: str
    who: str
    text: str
    due_text: str


class BriefingView(BaseModel):
    show: bool = Field(description="True when the owner has been away and there is something to tell.")
    away_minutes: int | None
    conversations: list[ConversationView]
    overdue: list[OverdueView]


class SendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(
        default=None, min_length=1, max_length=relay.MAX_TEXT, description="Your own words, instead of the draft."
    )


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/briefing",
        tags=["Owner: briefing"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "",
        summary="While you were away",
        description="Who wrote while you were away, what they need, what is overdue, with a drafted reply for each "
        "that you can send or dismiss. `show` is false unless you have been away a few hours and there is something "
        "to tell; `force=true` builds it anyway. Drafts are written once per message.",
    )
    def get_briefing(force: bool = Query(False), person_id: uuid.UUID = Depends(owner_person_id)) -> BriefingView:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            return BriefingView(**service.build(get_agent(), s, owner, force=force))

    @router.post("/seen", summary="Mark the briefing as read", status_code=204)
    def seen(person_id: uuid.UUID = Depends(owner_person_id)) -> None:
        with session_scope() as s:
            require(s, Person, person_id)
            service.mark_seen(s)

    def _act(person_id: uuid.UUID, work: Callable[..., Any]) -> DraftView:
        refused: service.BriefingError | None = None
        shown: dict[str, Any] = {}
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                shown = service.view_draft(work(s, owner))
            except service.BriefingError as exc:
                refused = exc  # raised after the commit: what was decided (a draft gone stale, a refused send) is kept
        if refused is not None:
            raise HTTPException(refused.status, str(refused)) from refused
        return DraftView(**shown)

    @router.post(
        "/drafts/{draft_id}/send",
        summary="Send a drafted reply",
        description="Your tap is the approval: one message goes to them through the same checks as any relayed "
        "message. Send `text` to use your own words. 409 if it was already sent or dismissed, they wrote again, or a "
        "check (paused, daily cap) stops it.",
    )
    def send(
        draft_id: uuid.UUID, body: SendBody | None = None, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> DraftView:
        return _act(person_id, lambda s, o: service.send(s, o, draft_id, body.text if body else None))

    @router.post(
        "/drafts/{draft_id}/dismiss", summary="Dismiss a drafted reply", description="Nothing is learned from it."
    )
    def dismiss(draft_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> DraftView:
        return _act(person_id, lambda s, o: service.dismiss(s, o, draft_id))

    return router
