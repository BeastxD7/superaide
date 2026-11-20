"""The owner's receptionist API (ADR 0010 TR5): the switch, the public profile, and messages taken.

Owner-only (`owner_person_id` puts the owner's instance in scope), CSRF-checked on changes. Not
behind TRIAGE_ENABLED: the receptionist is on by default whether or not triage is.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.identity.models import Person
from superaide.memory.models import Context
from superaide.platform.audit import audit
from superaide.platform.db.session import require, session_scope
from superaide.platform.tenancy.context import scope_or_default
from superaide.reception import ask, card, takeover, takeover_send
from superaide.reception.models import ReceptionMessage, ReceptionSettings, VisitorQuestion
from superaide.reception.service import card_preferences, greeting, settings_for
from superaide.reception.turn import has_card


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
    urgent_over_cap: bool | None = None
    takeover_idle_minutes: int | None = Field(default=None, ge=takeover.MIN_MINUTES, le=takeover.MAX_MINUTES)


class WriteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=takeover.MAX_TEXT)


class PreviewLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["visitor", "ava"]
    text: str = Field(max_length=1000)


class PreviewBody(BaseModel):
    """Lines a visitor would write, run in order through the real receptionist inside a sandbox (#499). `history` is
    what was said before (replayed without a model call, so a longer chat is not paid for twice)."""

    model_config = ConfigDict(extra="forbid")

    channel: Literal["telegram", "slack"] = "telegram"
    history: list[PreviewLine] = Field(default_factory=list, max_length=20)
    messages: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(min_length=1, max_length=8)


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=ask.ANSWER_CHARS)


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
        "urgent_over_cap": rs.urgent_over_cap,
        "takeover_idle_minutes": takeover.window_minutes(s, owner.id),  # 0: the deployment turned takeover off
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


def question_view(q: VisitorQuestion, name: str) -> dict[str, Any]:
    """`question` and `name` are the visitor's words (unverified): the app shows them quoted."""
    return {
        "id": str(q.id), "provider": q.provider, "name": name, "question": q.question, "state": q.state,
        "answer": q.answer, "delivery": q.delivery, "created_at": q.created_at.isoformat(),
    }  # fmt: skip


def _name_of(s: Session, identity_id: uuid.UUID) -> str:
    row = s.scalar(
        select(ReceptionMessage.name)
        .where(ReceptionMessage.identity_id == identity_id, ReceptionMessage.name != "")
        .order_by(ReceptionMessage.created_at.desc())
        .limit(1)
    )
    word = ((row or "").split() or [""])[0]
    return "".join(c for c in word if c.isalpha() or c in "'-")[:20].strip("'-")


def _question(s: Session, question_id: uuid.UUID) -> VisitorQuestion:
    q = s.scalar(
        select(VisitorQuestion).where(
            VisitorQuestion.id == question_id, VisitorQuestion.agent_id == scope_or_default().agent_id
        )
    )
    if q is None:
        raise HTTPException(404, "Not found")
    return q


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
        description="What people Ava doesn't know asked her to pass on (newest first, any age). All unverified.",
    )
    def messages(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            rows = s.scalars(
                select(ReceptionMessage)
                .where(
                    ReceptionMessage.agent_id == scope_or_default().agent_id,
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

    @router.get(
        "/receptionist/preview/scripts",
        summary="Standard preview scripts",
        description="Visitor lines to replay through the preview, for example after changing a setting.",
    )
    def preview_scripts(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        from superaide.reception import sandbox

        return {"scripts": sandbox.standard_scripts()}

    @router.post(
        "/receptionist/preview",
        summary="Try the receptionist as a visitor",
        description="Runs what a stranger would write through the real receptionist inside a sandbox: nothing is sent, "
        "nothing is stored as a visitor's message, no notification reaches you. The reply is what they would "
        "get; the model's cost is counted.",
    )
    def preview(body: PreviewBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        from superaide.reception import sandbox

        return sandbox.preview(
            get_agent(), person_id, channel=body.channel, history=[h.model_dump() for h in body.history],
            messages=body.messages,
        )  # fmt: skip

    @router.get(
        "/receptionist/questions",
        summary="Questions waiting for you",
        description="Things people Ava doesn't know asked that she couldn't answer, newest first.",
    )
    def questions(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            ask.expire_old(s)
            rows = s.scalars(
                select(VisitorQuestion)
                .where(
                    VisitorQuestion.agent_id == scope_or_default().agent_id,
                )
                .order_by(VisitorQuestion.created_at.desc())
                .limit(100)
            ).all()
            return {
                "questions": [question_view(q, _name_of(s, q.identity_id)) for q in rows],
                "open": sum(1 for q in rows if q.state == "asked"),
            }

    @router.post(
        "/receptionist/questions/{question_id}/answer",
        summary="Answer a visitor's question",
        description="Sends your answer to that person inside a fixed sentence, once. Nothing is saved for others.",
    )
    def answer(question_id: uuid.UUID, body: AnswerBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict:
        with session_scope() as s:
            q = _question(s, question_id)
            taken, notice = ask.answer(s, q, body.text)
            if not taken:
                raise HTTPException(409, notice)
            return {**question_view(q, _name_of(s, q.identity_id)), "notice": notice}

    @router.post("/receptionist/questions/{question_id}/decline", summary="Don't answer a visitor's question")
    def decline(question_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict:
        with session_scope() as s:
            q = _question(s, question_id)
            problem = ask.decline(s, q)
            if problem:
                raise HTTPException(409, problem)
            return question_view(q, _name_of(s, q.identity_id))

    @router.get(
        "/receptionist/conversations",
        summary="Conversations you can step into",
        description=(
            "People Ava doesn't know who wrote, one row each, newest first, with whether you are "
            "handling the conversation yourself (Ava stays out of it until you hand it back or you go quiet)."
        ),
    )
    def conversations(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            if not takeover.enabled():
                raise HTTPException(404, "Not found")
            rows = s.execute(
                select(ReceptionMessage.context_id, func.max(ReceptionMessage.created_at))
                .where(
                    ReceptionMessage.agent_id == scope_or_default().agent_id,
                )
                .group_by(ReceptionMessage.context_id)
                .order_by(func.max(ReceptionMessage.created_at).desc())
                .limit(50)
            ).all()
            out = []
            for context_id, last in rows:
                ctx = s.get(Context, context_id)
                if ctx is None:
                    continue
                row = takeover.active(s, context_id)
                out.append(
                    {
                        "context_id": str(context_id),
                        "provider": ctx.provider,
                        "name": takeover.visitor_name(s, ctx),
                        "last_at": last.isoformat(),
                        "takeover": takeover.view(s, row) if row else None,
                        "messages": takeover.recent(s, context_id) if row else [],
                    }  # fmt: skip
                )
            return {"conversations": out, "handling": sum(1 for c in out if c["takeover"]),
                    "idle_minutes": takeover.window_minutes(s, person_id)}  # fmt: skip

    def _visitor_context(s: Session, context_id: uuid.UUID, owner_id: uuid.UUID) -> Context:
        ctx = s.scalar(select(Context).where(Context.id == context_id, Context.agent_id == scope_or_default().agent_id))
        if not takeover.enabled() or ctx is None or not takeover.is_visitor_context(s, ctx, owner_id):
            raise HTTPException(404, "Not found")
        return ctx

    @router.post(
        "/receptionist/conversations/{context_id}/write",
        summary="Write to a visitor yourself",
        description=(
            "Sends your own words to that person (labelled as from you, not Ava) and takes the conversation over: Ava "
            "does not reply there, but reads what you wrote, until you hand it back or stay silent for the idle window."
        ),
    )
    def write(context_id: uuid.UUID, body: WriteBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            ctx = _visitor_context(s, context_id, owner.id)
            try:
                taken, delivered = takeover_send.write(s, ctx, owner, body.text)
            except takeover_send.RateLimited as exc:
                raise HTTPException(429, str(exc)) from exc
            if not taken:
                raise HTTPException(409, "Ava is paused, or there was nothing to send.")
            row = takeover.active(s, ctx.id)
            return {
                "delivered": delivered,
                "notice": "" if delivered else takeover_send.UNDELIVERED,
                "takeover": takeover.view(s, row) if row else None,
            }  # fmt: skip

    @router.post(
        "/receptionist/conversations/{context_id}/release",
        summary="Give control back to Ava",
        description="Ava answers this conversation again from the visitor's next message.",
    )
    def release(context_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict:
        with session_scope() as s:
            ctx = _visitor_context(s, context_id, person_id)
            row = takeover.active(s, ctx.id)
            if row is not None:
                takeover.release(s, row, "owner")
            return {"released": row is not None}

    return router
