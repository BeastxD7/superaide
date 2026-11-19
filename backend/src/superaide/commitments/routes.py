"""The owner's promises ledger API (#480): list, add, change, close, remove (soft). Owner-only, CSRF-checked on changes.
Nothing here is reachable by anyone Ava talks to, and nothing is hard-deleted."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from superaide.app.deps import owner_person_id
from superaide.commitments import service
from superaide.commitments.models import Commitment
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.identity.models import Person
from superaide.intentions.service import describe_due, owner_zone
from superaide.platform.db.session import require, session_scope
from superaide.platform.pagination import Page, SortKey, bind_for, cursor_param, limit_param, paginate


class CommitmentView(BaseModel):
    id: str
    debtor: str
    who: str
    provider: str
    text: str
    due_at: datetime
    due_text: str
    state: str
    source: str
    closed_at: datetime | None
    closed_by: str
    can_reply: bool = Field(description="True when the promise is to a conversation the owner can write into.")


class CommitmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=service.MAX_TEXT)
    due_at: datetime
    who: str = Field(default="", max_length=service.MAX_LABEL)
    debtor: Literal["owner", "ava"] = "owner"


class CommitmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=service.MAX_TEXT)
    due_at: datetime | None = None
    who: str | None = Field(default=None, max_length=service.MAX_LABEL)


def view(c: Commitment, tz: str) -> CommitmentView:
    return CommitmentView(
        id=str(c.id), debtor=c.debtor, who=c.creditor_label, provider=c.provider, text=c.text, due_at=c.due_at,
        due_text=describe_due(c.due_at, tz), state=c.state, source=c.source, closed_at=c.closed_at,
        closed_by=c.closed_by, can_reply=c.context_id is not None,
    )  # fmt: skip


def _run(person_id: uuid.UUID, work: Any) -> CommitmentView:
    with session_scope() as s:
        owner = require(s, Person, person_id)
        try:
            c = work(s, owner)
        except service.CommitmentError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return view(c, owner_zone(s, owner))


def make_router() -> APIRouter:
    router = APIRouter(
        prefix="/app/commitments",
        tags=["Owner: promises"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "",
        summary="Promises",
        description="What you or Ava promised someone and by when. `view=active` (default) is what is still to do, "
        "soonest first; `view=closed` is what was done or cancelled, newest first. Paged by cursor.",
        response_model=Page[CommitmentView],
    )
    def list_commitments(
        view_: Literal["active", "closed"] = Query("active", alias="view"),
        limit: int = limit_param(50, 100),
        cursor: str | None = cursor_param(),
        person_id: uuid.UUID = Depends(owner_person_id),
    ) -> Page[CommitmentView]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            tz = owner_zone(s, owner)
            closed = view_ == "closed"
            keys = [SortKey(Commitment.due_at, desc=closed), SortKey(Commitment.id, desc=closed)]
            return paginate(
                s, service.listing(owner.id, closed=closed), keys, endpoint=f"commitments:{view_}",
                bind=bind_for(person_id), limit=limit, cursor=cursor, build=lambda c: view(c, tz),
            )  # fmt: skip

    @router.post("", summary="Add a promise", status_code=201)
    def add(body: CommitmentBody, person_id: uuid.UUID = Depends(owner_person_id)) -> CommitmentView:
        return _run(
            person_id,
            lambda s, o: service.create(s, o, text=body.text, due_at=body.due_at, who=body.who, debtor=body.debtor),
        )

    @router.patch("/{commitment_id}", summary="Change a promise", description="Only the fields sent change.")
    def patch(
        commitment_id: uuid.UUID, body: CommitmentPatch, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> CommitmentView:
        return _run(
            person_id,
            lambda s, o: service.change(s, o, commitment_id, text=body.text, due_at=body.due_at, who=body.who),
        )

    @router.post("/{commitment_id}/done", summary="Mark a promise kept")
    def done(commitment_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> CommitmentView:
        return _run(person_id, lambda s, o: service.close(s, o, commitment_id, state="done"))

    @router.post("/{commitment_id}/cancel", summary="Cancel a promise")
    def cancel(commitment_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> CommitmentView:
        return _run(person_id, lambda s, o: service.close(s, o, commitment_id, state="cancelled"))

    @router.delete(
        "/{commitment_id}",
        summary="Remove a promise from the list",
        description="Soft delete: it leaves your list and its reminders stop, but the record is kept.",
    )
    def remove(commitment_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> CommitmentView:
        return _run(person_id, lambda s, o: service.remove(s, o, commitment_id))

    return router
