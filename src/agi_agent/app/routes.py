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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agi_agent.app import accounts
from agi_agent.app.catalog import catalog, find
from agi_agent.app.onboarding import Onboarder, documents_view
from agi_agent.auth.routes import current_user_id, require_csrf_header
from agi_agent.config import get_settings
from agi_agent.db.models import AgentHire, Context, Document, Event, MemoryItem, Person, UserAccount
from agi_agent.db.session import session_scope
from agi_agent.factory import uses_real_model
from agi_agent.knowledge import extract
from agi_agent.knowledge.ingest import ingest
from agi_agent.knowledge.store import retire_document
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


def _require_text(extracted: extract.Extracted) -> None:
    """Scanned PDFs and script-only pages come back with no text: say why instead of saving nothing."""
    if not extracted.text.strip():
        raise HTTPException(422, " ".join(extracted.warnings) or "I couldn't find any text in that.")


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


class LinkBody(BaseModel):
    url: str = Field(min_length=4, max_length=2048)


class CallAnswerBody(BaseModel):
    question_id: str = Field(max_length=10)
    transcript: str = Field(default="", max_length=8000)


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/app", dependencies=[Depends(require_csrf_header)])

    def onboarder() -> Onboarder:
        agent = get_agent()  # its settings include credentials connected in the app
        return Onboarder(agent, telegram_username(agent.settings.telegram_bot_token))

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
        settings = get_agent().settings
        with session_scope() as s:
            person = s.get(Person, person_id)
            state = onboarder().state(s, person)
            providers = accounts.linked_providers(s, person)
            return {
                "person": {"id": str(person.id), "name": person.display_name},
                "agent": {"name": settings.agent_name, "role": "Personal assistant", "stage": "Infant"},
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
            return ob.answer(s, s.get(Person, person_id), body.step, body.answer).as_dict()

    # --- introducing yourself with files, links or a video call -------------------

    def _said(person_id: uuid.UUID, line: str) -> None:
        with session_scope() as s:
            onboarder()._say(s, s.get(Person, person_id), [line])

    @router.post("/intro/files")
    async def intro_file(
        file: UploadFile = File(...), person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        data = await file.read(extract.MAX_FILE_BYTES + 1)
        try:
            extracted = extract.extract_file(file.filename or "upload", data)
        except extract.ExtractError as exc:
            raise HTTPException(422, str(exc)) from exc
        _require_text(extracted)
        result = ingest(get_agent(), person_id, extracted, size_bytes=len(data))
        if not result.duplicate:
            _said(person_id, result.message)
        return {**result.__dict__, "document_id": str(result.document_id), "warnings": list(extracted.warnings)}

    @router.post("/intro/links")
    def intro_link(body: LinkBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        try:
            extracted = extract.extract_url(body.url.strip())
        except extract.ExtractError as exc:
            raise HTTPException(422, str(exc)) from exc
        _require_text(extracted)
        result = ingest(get_agent(), person_id, extracted, size_bytes=len(extracted.text))
        if not result.duplicate:
            _said(person_id, result.message)
        return {**result.__dict__, "document_id": str(result.document_id), "warnings": list(extracted.warnings)}

    @router.get("/intro/documents")
    def intro_documents(person_id: uuid.UUID = Depends(owner_person_id)) -> list[dict[str, Any]]:
        with session_scope() as s:
            return documents_view(s, s.get(Person, person_id))

    @router.post("/intro/documents/{document_id}/remove")
    def remove_document(document_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            doc = s.get(Document, document_id)
            if doc is None or doc.owner_person_id != person_id or doc.removed_at is not None:
                raise HTTPException(404, "Not found")
            return {"status": "removed", "facts_removed": retire_document(s, doc)}

    @router.get("/intro/call")
    def call_start(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        return onboarder().call_start()

    @router.post("/intro/call/answer")
    def call_answer(body: CallAnswerBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                return onboarder().call_answer(s, s.get(Person, person_id), body.question_id, body.transcript)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @router.post("/intro/call/finish")
    def call_finish(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        with session_scope() as s:
            onboarder().call_finish(s, s.get(Person, person_id))
        return {"status": "ok"}

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
        """Everything the agent currently knows about the owner, newest first, with where it came from."""
        with session_scope() as s:
            rows = s.execute(
                select(MemoryItem, Document.title)
                .outerjoin(Document, Document.id == MemoryItem.document_id)
                .where(MemoryItem.subject_person_ids.contains([person_id]), MemoryItem.valid_to.is_(None))
                .order_by(MemoryItem.created_at.desc())
                .limit(300)
            ).all()
            return {
                "memories": [
                    {
                        "id": str(m.id),
                        "text": m.content,
                        "category": m.category,
                        "source": m.source,
                        "document": title,
                        "shareable": m.allowed_kind == "public",
                        "private": m.allowed_kind == "persons",
                        "at": m.created_at.isoformat(),
                    }
                    for m, title in rows
                ]
            }

    @router.post("/notebook/{memory_id}/forget")
    def forget(memory_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        with session_scope() as s:
            m = s.get(MemoryItem, memory_id)
            # You can remove what Ava knows about you, and only that.
            if m is None or person_id not in (m.subject_person_ids or []) or m.valid_to is not None:
                raise HTTPException(404, "Not found in your notebook")
            m.valid_to = datetime.now(UTC)
            from agi_agent.audit import audit

            audit(s, "app", "memory_forgotten", memory_id=m.id, person_id=person_id)
        return {"status": "forgotten"}

    @router.post("/channels/telegram")
    def connect_telegram(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        settings = get_agent().settings
        username = telegram_username(settings.telegram_bot_token)
        if not username:
            return {"available": False}
        with session_scope() as s:
            code = accounts.create_invite(s, s.get(Person, person_id), "telegram")
        return {"available": True, "url": f"https://t.me/{username}?start={code}", "expires_minutes": 15}

    return router
