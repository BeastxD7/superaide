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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.identity.models import Person
from superaide.platform.audit import audit
from superaide.platform.db.session import require, session_scope
from superaide.platform.tenancy.context import scope_or_default
from superaide.reception import card
from superaide.reception.models import ReceptionMessage, ReceptionSettings
from superaide.reception.service import card_preferences, greeting, settings_for
from superaide.reception.turn import has_card

WINDOW = timedelta(days=30)


class CardBody(BaseModel):
    """The share card (#475): a value and its own switch, each optional; only what is sent changes."""

    model_config = ConfigDict(extra="forbid")

    company: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    company_public: bool | None = None
    role_public: bool | None = None
    city_public: bool | None = None
    email_public: bool | None = None


class SettingsBody(BaseModel):
    card: CardBody | None = None
    enabled: bool | None = None
    show_first_name: bool | None = None
    public_profile: str | None = Field(default=None, max_length=2000)
    per_hour: int | None = Field(default=None, ge=1, le=60)
    per_day: int | None = Field(default=None, ge=1, le=500)


class StatusBody(BaseModel):
    status: Literal["new", "done", "dismissed", "spam"]


def view(s: Session, rs: ReceptionSettings, owner: Person, agent_name: str) -> dict[str, Any]:
    pref = card.preference(s, owner)
    return {
        # The share card: each value with its own switch (off by default), exactly what she is told (`told`), and
        # whether she has anything at all to share (`empty`).
        "card": {
            "fields": card.view(pref),
            "told": card.lines(pref),
            "empty": not has_card(s, owner, rs),
            "place": card.PLACE,
        },
        "enabled": rs.enabled,
        "show_first_name": rs.show_first_name,
        "public_profile": rs.public_profile,
        "per_hour": rs.per_hour,
        "per_day": rs.per_day,
        "greeting": greeting(
            agent_name, owner, rs, has_card=has_card(s, owner, rs)
        ),  # what a stranger's first reply starts with
    }


def message_view(m: ReceptionMessage) -> dict[str, Any]:
    return {
        "id": str(m.id), "provider": m.provider, "handle": m.handle, "name": m.name, "contact": m.contact,
        "topic": m.topic, "message": m.message, "deadline_text": m.deadline_text,
        "deadline_at": m.deadline_at.isoformat() if m.deadline_at else None, "deadline_parse": m.deadline_parse,
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
            return view(s, settings_for(s, owner), owner, get_agent().name)

    @router.put("/receptionist", summary="Change receptionist settings", description="Only the fields sent change.")
    def put_receptionist(body: SettingsBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            rs = settings_for(s, owner)
            changes = body.model_dump(exclude_none=True, exclude={"card"})
            if body.card is not None:
                pref = card_preferences(s, owner)
                edits = body.card.model_dump(exclude_none=True)
                try:
                    for key, v in edits.items():
                        if key.endswith("_public"):
                            setattr(pref, f"card_{key}", bool(v))
                        else:
                            setattr(pref, f"card_{key}", card.clean(key, v))
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                audit(s, "reception", "card_changed", fields=sorted(edits))  # names only, never the values
            if "public_profile" in changes:
                changes["public_profile"] = changes["public_profile"].strip()
            for k, v in changes.items():
                setattr(rs, k, v)
            audit(s, "reception", "settings_changed", fields=sorted(changes))
            return view(s, rs, owner, get_agent().name)

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
