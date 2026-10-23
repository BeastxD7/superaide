"""The owner's rehearsal API (#58). Owner-only, CSRF-checked, per instance; 404 while REHEARSAL_ENABLED is off.

Nothing here sends a message anywhere or writes memory. A session lives for a day unless kept.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from superaide.app.routes import owner_person_id
from superaide.audit import audit
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.brain.budget import AgentFrozen, BudgetExceeded
from superaide.db.session import require, session_scope
from superaide.rehearsal import service
from superaide.rehearsal.models import RehearsalSession
from superaide.tenancy.context import scope_or_default

KEEP_FOR = timedelta(days=1)
PAUSED = "Practice is paused right now. {why} Your session is saved as it was; try again later."


class StartBody(BaseModel):
    role: str = Field(min_length=2, max_length=120)  # "a sceptical investor", "an upset customer"
    notes: str = Field(default="", max_length=4000)


class TurnBody(BaseModel):
    text: str = Field(min_length=1, max_length=service.MAX_TURN_CHARS)


def view(row: RehearsalSession) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "role": row.role,
        "notes": row.notes,
        "turns": row.turns,
        "debrief": row.debrief,
        "kept": row.kept,
        "ended": row.ended_at is not None,
        "expires_at": None if row.kept else (row.created_at + KEEP_FOR).isoformat(),
        "created_at": row.created_at.isoformat(),
        "notice": "This is a rehearsal. Ava is playing a role. Nothing is sent to anyone.",
    }


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        """The owner, once rehearsal is known to be on (checked after sign-in, so others get 401/403 as usual)."""
        if not get_agent().settings.rehearsal_enabled:
            raise HTTPException(404, "Not Found")
        return person_id

    router = APIRouter(
        prefix="/app/rehearsal",
        tags=["Owner: rehearsal"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def _purge(s: Any) -> None:
        """Unkept sessions older than a day are erased."""
        s.execute(
            delete(RehearsalSession).where(
                RehearsalSession.agent_id == scope_or_default().agent_id,
                RehearsalSession.kept.is_(False),
                RehearsalSession.created_at < datetime.now(UTC) - KEEP_FOR,
            )
        )

    def _mine(s: Any, person_id: uuid.UUID, session_id: uuid.UUID) -> RehearsalSession:
        _purge(s)
        row = s.scalar(
            select(RehearsalSession).where(
                RehearsalSession.id == session_id,
                RehearsalSession.owner_person_id == person_id,
                RehearsalSession.agent_id == scope_or_default().agent_id,
            )
        )
        if row is None:
            raise HTTPException(404, "Not found")
        return row

    def _model_ready() -> bool:
        """A real model, or a scripted one in tests; the offline echo can't play a role."""
        from superaide.factory import brain_ready, demo_responder

        agent = get_agent()
        return brain_ready(agent.settings) or getattr(agent.llm, "responder", None) is not demo_responder

    def _paused(why: str) -> dict[str, Any]:
        return {"paused": True, "message": PAUSED.format(why=why)}

    @router.get("", summary="Your rehearsals", description="Your practice sessions, newest first.")
    def list_sessions(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            _purge(s)
            rows = s.scalars(
                select(RehearsalSession)
                .where(
                    RehearsalSession.owner_person_id == person_id,
                    RehearsalSession.agent_id == scope_or_default().agent_id,
                )
                .order_by(RehearsalSession.created_at.desc())
                .limit(50)
            ).all()
            return {"sessions": [view(r) for r in rows]}

    @router.post("", summary="Start a rehearsal", description="Pick a generic role and add any notes for this session.")
    def start(body: StartBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            _purge(s)
            row = RehearsalSession(owner_person_id=person_id, role=body.role.strip(), notes=body.notes.strip())
            s.add(row)
            s.flush()
            audit(s, "owner", "rehearsal_started", session_id=str(row.id))  # an id, never the words
            return view(row)

    @router.get("/{session_id}", summary="One rehearsal")
    def get_one(session_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return view(_mine(s, person_id, session_id))

    @router.post("/{session_id}/turn", summary="Say your line", description="Ava answers as the other side.")
    def turn(session_id: uuid.UUID, body: TurnBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        agent = get_agent()
        with session_scope() as s:
            row = _mine(s, person_id, session_id)
            if row.ended_at is not None:
                raise HTTPException(409, "This rehearsal has ended. Start another one to keep practising.")
            if sum(1 for t in row.turns if t["who"] == "owner") >= service.MAX_TURNS:
                raise HTTPException(409, "That is a long rehearsal. End it to get your debrief.")
            if not _model_ready():
                return {**_paused("No AI model is connected."), "session": view(row)}
            try:
                reply = service.play(agent.llm, agent.settings.model_chat, row, body.text)
            except (BudgetExceeded, AgentFrozen) as e:
                return {**_paused(str(e)), "session": view(row)}  # the turn isn't saved
            row.turns = [*row.turns, {"who": "owner", "text": body.text}, {"who": "other", "text": reply}]
            return {"paused": False, "reply": reply, "session": view(row)}

    @router.post("/{session_id}/end", summary="End and get the debrief", description="Three points, each quoting you.")
    def end(session_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        agent = get_agent()
        with session_scope() as s:
            row = _mine(s, person_id, session_id)
            if row.debrief is not None:
                return {"paused": False, "session": view(row)}
            if not any(t["who"] == "owner" for t in row.turns):
                raise HTTPException(409, "Say something first, then ask for the debrief.")
            if not _model_ready():
                return {**_paused("No AI model is connected."), "session": view(row)}
            try:
                row.debrief = service.debrief(agent.llm, agent.settings.model_chat, row)
            except (BudgetExceeded, AgentFrozen) as e:
                return {**_paused(str(e)), "session": view(row)}
            row.ended_at = datetime.now(UTC)
            return {"paused": False, "session": view(row)}

    @router.post("/{session_id}/keep", summary="Keep this rehearsal", description="Otherwise it is erased after a day.")
    def keep(session_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            row = _mine(s, person_id, session_id)
            row.kept = True
            return view(row)

    @router.delete(
        "/{session_id}", summary="Erase this rehearsal", description="Gone for good: the transcript and the debrief."
    )
    def erase(session_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            row = _mine(s, person_id, session_id)
            s.delete(row)
            audit(s, "owner", "rehearsal_erased", session_id=str(session_id))
            return {"ok": True}

    _ = require  # imported for symmetry with other routers
    return router
