"""The person-facing app API: marketplace, hiring, onboarding, chat, notebook.

Signed-in requests carry the session cookie set by /auth (see superaide.auth).
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

from superaide.app import accounts
from superaide.app.catalog import catalog, find
from superaide.app.onboarding import SHARE_LABELS, Onboarder, documents_view
from superaide.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.config import get_settings
from superaide.db.models import AgentHire, ChannelIdentity, Context, Document, Event, MemoryItem, Person, UserAccount
from superaide.db.session import require, session_scope
from superaide.factory import uses_real_model
from superaide.knowledge import extract
from superaide.knowledge.ingest import ingest
from superaide.knowledge.store import contacts_of, preferences_for, retire_document
from superaide.policy.killswitch import is_frozen


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
        return require(s, UserAccount, user_id).person_id


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
    router = APIRouter(prefix="/app", dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)])

    def onboarder() -> Onboarder:
        agent = get_agent()  # its settings include credentials connected in the app
        return Onboarder(agent, telegram_username(agent.settings.telegram_bot_token))

    @router.get(
        "/agents",
        tags=["Marketplace & hiring"],
        summary="List agents",
        description="The agents on offer, whether each is available, and who it works for if it has been hired.",
    )
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

    @router.post(
        "/hire",
        tags=["Marketplace & hiring"],
        summary="Hire Ava",
        description=(
            "Hires the agent for the signed-in account after the notice is accepted. Refused if someone else already "
            "hired it."
        ),
    )
    def hire(body: HireBody, user_id: uuid.UUID = Depends(current_user_id)) -> dict[str, Any]:
        settings = get_settings()
        listing = find(settings, body.agent)
        if listing is None or listing["status"] != "available":
            raise HTTPException(404, "That agent can't be hired yet")
        if not body.accepted_notice:
            raise HTTPException(400, "Please read and accept how the agent handles what you tell it")
        try:
            with session_scope() as s:
                user = require(s, UserAccount, user_id)
                try:
                    person = accounts.hire(s, user, body.agent)
                except accounts.AlreadyHired as exc:
                    # Not the owner's name: any signed-up account can ask, and it's not theirs to know.
                    raise HTTPException(409, f"{settings.agent_name} already works for someone else.") from exc
                onboarder().ensure_started(s, person)
                return {"hired": body.agent, "person": {"id": str(person.id), "name": person.display_name}}
        except IntegrityError as exc:  # someone else hired at the same moment
            raise HTTPException(409, f"{settings.agent_name} was just hired by someone else.") from exc

    @router.get(
        "/me",
        tags=["Owner: chat & notebook"],
        summary="My agent and settings",
        description="The owner's agent, growth stage, connected channels and introduction progress.",
    )
    def me(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        settings = get_agent().settings
        with session_scope() as s:
            person = require(s, Person, person_id)
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

    @router.get(
        "/onboarding",
        tags=["Introduction"],
        summary="Current introduction step",
        description="Where the owner is in the eight steps, the conversation so far and the controls for the step.",
    )
    def onboarding(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        ob = onboarder()
        with session_scope() as s:
            person = require(s, Person, person_id)
            ob.ensure_started(s, person)
            return ob.view(s, person).as_dict()

    @router.post(
        "/onboarding",
        tags=["Introduction"],
        summary="Answer a step",
        description=(
            "Saves the answer to the current step and moves on. Each step's `answer` shape is in the step's `input`."
        ),
    )
    def onboarding_answer(body: AnswerBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        ob = onboarder()
        with session_scope() as s:
            return ob.answer(s, require(s, Person, person_id), body.step, body.answer).as_dict()

    # --- introducing yourself with files, links or a video call -------------------

    def _said(person_id: uuid.UUID, line: str) -> None:
        with session_scope() as s:
            onboarder()._say(s, require(s, Person, person_id), [line])

    @router.post(
        "/intro/files",
        tags=["Introduction"],
        summary="Share a file",
        description=(
            "A resume or CV (PDF, Word, text; up to 10 MB). The text is kept and read into notebook facts; offline it"
            " is read once Claude is connected."
        ),
    )
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

    @router.post(
        "/intro/links",
        tags=["Introduction"],
        summary="Share a link",
        description=(
            "A website, GitHub profile or page about the owner. Private addresses are refused; LinkedIn is explained "
            "(it blocks reading)."
        ),
    )
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

    @router.get(
        "/intro/documents",
        tags=["Introduction"],
        summary="Shared files and links",
        description="Everything the owner shared, whether it has been read, and how many facts came from each.",
    )
    def intro_documents(person_id: uuid.UUID = Depends(owner_person_id)) -> list[dict[str, Any]]:
        with session_scope() as s:
            return documents_view(s, require(s, Person, person_id))

    @router.post(
        "/intro/documents/{document_id}/remove",
        tags=["Introduction"],
        summary="Remove a file or link",
        description="Removes it and everything Ava learned from it.",
    )
    def remove_document(document_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            doc = s.get(Document, document_id)
            if doc is None or doc.owner_person_id != person_id or doc.removed_at is not None:
                raise HTTPException(404, "Not found")
            return {"status": "removed", "facts_removed": retire_document(s, doc)}

    @router.get(
        "/intro/call",
        tags=["Introduction"],
        summary="Start the video call",
        description="The call's questions. The browser turns the owner's speech into text; nothing is recorded.",
    )
    def call_start(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        return onboarder().call_start()

    @router.post(
        "/intro/call/answer",
        tags=["Introduction"],
        summary="Answer a call question",
        description="One transcribed answer; returns the next question.",
    )
    def call_answer(body: CallAnswerBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                return onboarder().call_answer(s, require(s, Person, person_id), body.question_id, body.transcript)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @router.post(
        "/intro/call/finish",
        tags=["Introduction"],
        summary="Finish the call",
        description="Ends the call and continues the introduction.",
    )
    def call_finish(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        with session_scope() as s:
            onboarder().call_finish(s, require(s, Person, person_id))
        return {"status": "ok"}

    @router.get(
        "/messages",
        tags=["Owner: chat & notebook"],
        summary="Conversation history",
        description="The owner's conversation with Ava, oldest first (introduction included).",
    )
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

    @router.post(
        "/chat",
        tags=["Owner: chat & notebook"],
        summary="Send a message",
        description="Sends a message to Ava and returns the reply. `remember that …` stores a private note.",
    )
    def chat(body: ChatBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        text = body.text.strip()
        if not text:
            raise HTTPException(422, "Type a message first.")
        with session_scope() as s:
            person = require(s, Person, person_id)
            msg = accounts.web_message(person, text)
        result = get_agent().handle(msg)
        return {"mode": result.mode, "reply": result.reply, "notes": result.notes}

    @router.get(
        "/notebook",
        tags=["Owner: chat & notebook"],
        summary="Notebook",
        description="Everything Ava currently knows about the owner, newest first, with its category and source.",
    )
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

    @router.get(
        "/people",
        tags=["Owner: people & rules"],
        summary="People who matter",
        description="The owner's contacts and who can always get through.",
    )
    def people(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        """The people who matter to the owner, VIPs first, and where Ava can recognise them."""
        with session_scope() as s:
            owner = require(s, Person, person_id)
            result = []
            for contact, person in contacts_of(s, owner):
                channels = sorted(
                    set(
                        s.scalars(
                            select(ChannelIdentity.provider).where(
                                ChannelIdentity.person_id == person.id, ChannelIdentity.lifecycle == "active"
                            )
                        )
                    )
                )
                result.append(
                    {
                        "id": str(contact.id),
                        "name": person.display_name,
                        "relationship": contact.relationship,
                        "vip": contact.vip,
                        "channels": channels,
                    }
                )
            return {"people": result}

    @router.get(
        "/preferences",
        tags=["Owner: people & rules"],
        summary="Standing rules",
        description="What counts as urgent, quiet hours, digest time and what may be shared.",
    )
    def preferences(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        """The owner's standing rules: what's urgent, quiet hours, how to reach them, what may be shared."""
        with session_scope() as s:
            pref = preferences_for(s, require(s, Person, person_id))
            return {
                "urgent_topics": list(pref.urgent_topics),
                "digest": pref.digest,
                "interrupt_channel": pref.interrupt_channel,
                "quiet_start": pref.quiet_start,
                "quiet_end": pref.quiet_end,
                "vip_breaks_quiet": pref.vip_breaks_quiet,
                "timezone": pref.timezone,
                "shareable": [{"value": v, "label": SHARE_LABELS.get(v, v)} for v in pref.shareable],
                "never_share": pref.never_share,
            }

    @router.post(
        "/notebook/{memory_id}/forget",
        tags=["Owner: chat & notebook"],
        summary="Forget a note",
        description="Ava stops using this note.",
    )
    def forget(memory_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        with session_scope() as s:
            m = s.get(MemoryItem, memory_id)
            # You can remove what Ava knows about you, and only that.
            if m is None or person_id not in (m.subject_person_ids or []) or m.valid_to is not None:
                raise HTTPException(404, "Not found in your notebook")
            m.valid_to = datetime.now(UTC)
            from superaide.audit import audit

            audit(s, "app", "memory_forgotten", memory_id=m.id, person_id=person_id)
        return {"status": "forgotten"}

    @router.post(
        "/channels/telegram",
        tags=["Connections"],
        summary="Link my Telegram",
        description="A one-time `t.me` link (15 minutes) that links the owner's Telegram account to Ava.",
    )
    def connect_telegram(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        settings = get_agent().settings
        username = telegram_username(settings.telegram_bot_token)
        if not username:
            return {"available": False}
        with session_scope() as s:
            code = accounts.create_invite(s, require(s, Person, person_id), "telegram")
        return {"available": True, "url": f"https://t.me/{username}?start={code}", "expires_minutes": 15}

    return router
