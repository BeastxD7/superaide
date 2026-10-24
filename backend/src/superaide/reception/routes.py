"""The owner's receptionist API (ADR 0010 TR5): the switch, the public profile, and messages taken.

Owner-only (`owner_person_id` puts the owner's instance in scope), CSRF-checked on changes. Not
behind TRIAGE_ENABLED: the receptionist is on by default whether or not triage is.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.platform.audit import audit
from superaide.platform.db.models import Person
from superaide.platform.db.session import require, session_scope
from superaide.platform.tenancy.context import scope_or_default
from superaide.reception.models import ReceptionMessage, ReceptionSettings
from superaide.reception.service import greeting, settings_for

WINDOW = timedelta(days=30)


class SettingsBody(BaseModel):
    enabled: bool | None = None
    show_first_name: bool | None = None
    public_profile: str | None = Field(default=None, max_length=2000)
    per_hour: int | None = Field(default=None, ge=1, le=60)
    per_day: int | None = Field(default=None, ge=1, le=500)


class StatusBody(BaseModel):
    status: Literal["new", "done", "dismissed", "spam"]


def view(rs: ReceptionSettings, owner: Person, agent_name: str) -> dict[str, Any]:
    return {
        "enabled": rs.enabled,
        "show_first_name": rs.show_first_name,
        "public_profile": rs.public_profile,
        "per_hour": rs.per_hour,
        "per_day": rs.per_day,
        "greeting": greeting(agent_name, owner, rs),  # what a stranger's first reply starts with
    }


def message_view(m: ReceptionMessage) -> dict[str, Any]:
    return {
        "id": str(m.id), "provider": m.provider, "handle": m.handle, "name": m.name, "contact": m.contact,
        "topic": m.topic, "message": m.message, "deadline_text": m.deadline_text,
        "claimed_urgency": m.claimed_urgency, "rank": m.rank, "source": m.source, "status": m.status,
        "created_at": m.created_at.isoformat(),
    }  # fmt: skip


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app",
        tags=["Owner: receptionist"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "/receptionist",
        summary="Receptionist settings",
        description="Whether Ava talks with people she doesn't know, what she may tell them, and the limits.",
    )
    def get_receptionist(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            return view(settings_for(s, owner), owner, get_agent().name)

    @router.put("/receptionist", summary="Change receptionist settings", description="Only the fields sent change.")
    def put_receptionist(body: SettingsBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            rs = settings_for(s, owner)
            changes = body.model_dump(exclude_none=True)
            if "public_profile" in changes:
                changes["public_profile"] = changes["public_profile"].strip()
            for k, v in changes.items():
                setattr(rs, k, v)
            audit(s, "reception", "settings_changed", fields=sorted(changes))
            return view(rs, owner, get_agent().name)

    @router.get(
        "/receptionist/messages",
        summary="Messages taken",
        description="What people Ava doesn't know asked her to pass on (last 30 days). All of it is unverified.",
    )
    def messages(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            rows = s.scalars(
                select(ReceptionMessage)
                .where(
                    ReceptionMessage.agent_id == scope_or_default().agent_id,
                    ReceptionMessage.created_at >= datetime.now(UTC) - WINDOW,
                )
                .order_by(ReceptionMessage.created_at.desc())
                .limit(200)
            ).all()
            items = [message_view(m) for m in rows]
            return {"messages": items, "new": sum(1 for m in rows if m.status == "new")}

    @router.post("/receptionist/messages/{message_id}", summary="Mark a message taken")
    def mark(message_id: uuid.UUID, body: StatusBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict:
        with session_scope() as s:
            m = s.scalar(
                select(ReceptionMessage).where(
                    ReceptionMessage.id == message_id, ReceptionMessage.agent_id == scope_or_default().agent_id
                )
            )
            if m is None:
                raise HTTPException(404, "Not found")
            m.status, m.decided_at = body.status, datetime.now(UTC)
            audit(s, "reception", "message_marked", message_id=m.id, status=body.status)
            return message_view(m)

    return router
