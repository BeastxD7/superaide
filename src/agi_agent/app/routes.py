"""The person-facing app API: marketplace, hiring, onboarding, chat, notebook.

Signed-in requests carry the session cookie set by /auth (see agi_agent.auth).
Everything except the marketplace needs an account; everything after hiring
needs to be the account that hired the agent. The operator console uses the
separate ADMIN_TOKEN.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agi_agent.app import accounts
from agi_agent.app.catalog import catalog, find
from agi_agent.app.onboarding import Onboarder, state_for
from agi_agent.auth.routes import current_user_id, require_csrf_header
from agi_agent.config import get_settings
from agi_agent.context.audience import Audience, visible_memories
from agi_agent.db.models import AgentHire, Context, Event, MemoryItem, Person, UserAccount
from agi_agent.db.session import session_scope
from agi_agent.factory import uses_real_model
from agi_agent.policy.killswitch import is_frozen


@lru_cache
def telegram_username(token: str) -> str | None:
    """The bot's @username, needed for t.me links. None if Telegram isn't set up."""
    if not token:
        return None
    try:
        import httpx

        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        return r.json().get("result", {}).get("username")
    except Exception:
        return None


def owner_person_id(user_id: uuid.UUID = Depends(current_user_id)) -> uuid.UUID:
    """The signed-in account's Person, if that account hired the agent; otherwise 403."""
    with session_scope() as s:
        hire = s.scalar(select(AgentHire).where(AgentHire.owner_user_id == user_id, AgentHire.status == "active"))
        if hire is None:
            raise HTTPException(403, "Hire Ava first.")
        return s.get(UserAccount, user_id).person_id


class HireBody(BaseModel):
    agent: str = "ava"
    accepted_notice: bool


class AnswerBody(BaseModel):
    step: str
    answer: dict[str, Any] = Field(default_factory=dict)


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/app", dependencies=[Depends(require_csrf_header)])

    def onboarder() -> Onboarder:
        settings = get_settings()
        return Onboarder(get_agent(), telegram_username(settings.telegram_bot_token))

    @router.get("/agents")
    def agents() -> list[dict[str, Any]]:
        settings = get_settings()
        with session_scope() as s:
            owner = accounts.owner(s)
            hired_by = owner.display_name.split()[0] if owner else None
        result = []
        for a in catalog(settings):
            item = dict(a)
            if a["slug"] == "ava":
                item["hired_by"] = hired_by
                item["notice"] = accounts.NOTICE_TEXT
            result.append(item)
        return result

    @router.post("/hire")
    def hire(body: HireBody, user_id: uuid.UUID = Depends(current_user_id)) -> dict[str, Any]:
        settings = get_settings()
        if find(settings, body.agent) is None or find(settings, body.agent)["status"] != "available":
            raise HTTPException(404, "That agent can't be hired yet")
        if not body.accepted_notice:
            raise HTTPException(400, "Please read and accept how the agent handles what you tell it")
        try:
            with session_scope() as s:
                user = s.get(UserAccount, user_id)
                try:
                    person = accounts.hire(s, user, body.agent)
                except accounts.AlreadyHired as exc:
                    raise HTTPException(409, f"{settings.agent_name} already works for {exc.owner}.") from exc
                onboarder().ensure_started(s, person)
                return {"hired": body.agent, "person": {"id": str(person.id), "name": person.display_name}}
        except IntegrityError as exc:  # someone else hired at the same moment
            raise HTTPException(409, f"{settings.agent_name} was just hired by someone else.") from exc

    @router.get("/me")
    def me(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        settings = get_settings()
        with session_scope() as s:
            person = s.get(Person, person_id)
            state = state_for(s, person)
            providers = accounts.linked_providers(s, person)
            return {
                "person": {"id": str(person.id), "name": person.display_name},
                "agent": {"name": settings.agent_name, "role": "LinkedIn content strategist", "stage": "Infant"},
                "onboarding": {"step": state.step, "done": state.step == "done"},
                "brain": "claude" if uses_real_model(settings) else "offline",
                "paused": is_frozen(s),
                "channels": {
                    "web": True,
                    "telegram": "telegram" in providers,
                    "telegram_available": bool(telegram_username(settings.telegram_bot_token)),
                    "slack": "slack" in providers,
                },
            }

    @router.get("/onboarding")
    def onboarding(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        ob = onboarder()
        with session_scope() as s:
            person = s.get(Person, person_id)
            ob.ensure_started(s, person)
            return ob.view(s, person).as_dict()

    @router.post("/onboarding")
    def onboarding_answer(body: AnswerBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        ob = onboarder()
        with session_scope() as s:
            view, draft_topic = ob.answer(s, s.get(Person, person_id), body.step, body.answer)
            result = view.as_dict()
        if draft_topic:
            ob.draft_first_post(person_id, draft_topic)
        return result

    @router.get("/messages")
    def messages(person_id: uuid.UUID = Depends(owner_person_id), limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with session_scope() as s:
            ctx = s.scalar(select(Context).where(Context.provider == "web", Context.external_id == f"web:{person_id}"))
            if ctx is None:
                return []
            rows = s.scalars(
                select(Event).where(Event.context_id == ctx.id).order_by(Event.created_at.desc()).limit(limit)
            ).all()
            return [
                {
                    "id": str(e.id),
                    "from": "you" if e.direction == "in" else "agent",
                    "text": e.content,
                    "at": e.created_at.isoformat(),
                    "kind": "onboarding" if (e.meta or {}).get("onboarding") else "chat",
                }
                for e in reversed(rows)
            ]

    @router.post("/chat")
    def chat(body: ChatBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            person = s.get(Person, person_id)
            msg = accounts.web_message(person, body.text.strip())
        result = get_agent().handle(msg)
        return {"mode": result.mode, "reply": result.reply, "notes": result.notes}

    @router.get("/notebook")
    def notebook(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            aud = Audience(person_ids={person_id}, has_unknown=False, min_trust=2, all_internal=True)
            items = visible_memories(s, aud, limit=100)
            return {
                "memories": [
                    {
                        "id": str(m.id),
                        "text": m.content,
                        "private": m.allowed_kind == "persons",
                        "at": m.created_at.isoformat(),
                    }
                    for m in items
                ]
            }

    @router.post("/notebook/{memory_id}/forget")
    def forget(memory_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        with session_scope() as s:
            m = s.get(MemoryItem, memory_id)
            # You can remove what Ava remembers about you, and only that.
            if (
                m is None
                or person_id not in (m.allowed_person_ids or [])
                or person_id not in (m.subject_person_ids or [])
            ):
                raise HTTPException(404, "Not found in your notebook")
            m.valid_to = datetime.now(UTC)
            from agi_agent.audit import audit

            audit(s, "app", "memory_forgotten", memory_id=m.id, person_id=person_id)
        return {"status": "forgotten"}

    @router.post("/channels/telegram")
    def connect_telegram(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        settings = get_settings()
        username = telegram_username(settings.telegram_bot_token)
        if not username:
            return {"available": False}
        with session_scope() as s:
            code = accounts.create_invite(s, s.get(Person, person_id), "telegram")
        return {"available": True, "url": f"https://t.me/{username}?start={code}", "expires_minutes": 15}

    return router
