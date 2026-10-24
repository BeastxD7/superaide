"""The person-facing app API: marketplace, hiring, onboarding, chat, notebook.

Signed-in requests carry the session cookie set by /auth (see superaide.auth).
Everything except the marketplace needs an account; everything after hiring
needs to be the account that hired the agent. The operator console uses the
separate ADMIN_TOKEN.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from superaide.app import accounts
from superaide.app.catalog import catalog, find
from superaide.app.deps import owner_person_id, slack_connected, telegram_username
from superaide.app.onboarding import SHARE_LABELS, Onboarder, documents_view
from superaide.app.stream import stream_turn
from superaide.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.brain.wiring import brain_ready, brain_status
from superaide.channels import health
from superaide.config import get_settings
from superaide.db.models import (
    Approval,
    AuditLog,
    ChannelIdentity,
    Contact,
    Context,
    Document,
    DocumentChunk,
    Event,
    Intention,
    MemoryItem,
    Person,
    UserAccount,
)
from superaide.db.session import require, rescope, session_scope
from superaide.intentions import service as intentions
from superaide.intentions.when import clean_task, find_moment, zone
from superaide.knowledge import extract
from superaide.knowledge.erase import erase_document, erase_person
from superaide.knowledge.erase import vacuum as erase_vacuum
from superaide.knowledge.ingest import ingest
from superaide.knowledge.store import conflicts_of, preferences_for, resolve_conflict, retire_document
from superaide.pagination import SortKey, bind_for, cursor_param, encode_cursor, limit_param, paginate, set_next_headers
from superaide.policy import brake, kinds
from superaide.policy.actions import ApprovalError, resolve
from superaide.policy.binding import expired
from superaide.policy.killswitch import instance_frozen, is_frozen, platform_frozen, set_frozen
from superaide.skills import registry as skill_registry
from superaide.tenancy.context import use_instance
from superaide.tenancy.endpoints import slack_key
from superaide.voice import gateway


def _require_text(extracted: extract.Extracted) -> None:
    """Scanned PDFs and script-only pages come back with no text: say why instead of saving nothing."""
    if not extracted.text.strip():
        raise HTTPException(422, " ".join(extracted.warnings) or "I couldn't find any text in that.")


class ResolveConflict(BaseModel):
    choice: Literal["keep", "new", "custom"]
    text: str = Field(default="", max_length=2000)


class HireBody(BaseModel):
    agent: str = "ava"
    accepted_notice: bool


class AnswerBody(BaseModel):
    step: str
    answer: dict[str, Any] = Field(default_factory=dict)


class DecideBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AnswerQuestionBody(BaseModel):
    answer: str = Field(max_length=2000)


class SkipQuestionBody(BaseModel):
    never: bool = False


class CounterBody(BaseModel):
    on: bool


class DraftPlaybookBody(BaseModel):
    task: str = Field(min_length=3, max_length=500)


class ActivatePlaybookBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class OutcomeBody(BaseModel):
    used: bool
    distance: float = Field(ge=0, le=1)


class ReminderBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    # Either an exact moment, or a time in the text ("Call Priya tomorrow at 5").
    due_at: datetime | None = None


class InstallBody(BaseModel):
    # The permissions the owner saw on the consent screen; must match what the skill needs.
    accepted: list[str] = Field(default_factory=list, max_length=10)


class RecheckBody(BaseModel):
    answer: Literal["yes", "no", "changed"]
    new_content: str = Field(default="", max_length=2000)  # what is true now, for "changed"


class OptionBody(BaseModel):
    setting: str = Field(min_length=1, max_length=40)
    on: bool
    # Switching on: the extra permissions the owner saw on the consent screen.
    accepted: list[str] = Field(default_factory=list, max_length=10)


def skills_view(s: Any) -> list[dict[str, Any]]:
    """The skills list, plus whether Google is connected and still has to grant what calendar actions need."""
    from superaide.skills import calendar
    from superaide.skills import gmail as gmail_skill
    from superaide.vault.store import find

    cards = skill_registry.view(s)
    google_on = find(s, "google", "refresh_token") is not None
    for card in cards:
        # Capabilities that live on Google say whether Google is still connected: "installed" means nothing without it.
        if card["id"] in (calendar.SKILL_ID, gmail_skill.SKILL_ID):
            card["google_connected"] = google_on
        for option in card.get("options", []):
            if card["id"] == calendar.SKILL_ID and option["setting"] == calendar.OPTION:
                option["needs_google"] = bool(option["on"]) and not calendar.can_write(s)
    return cards


class SnoozeBody(BaseModel):
    minutes: int = Field(ge=1, le=60 * 24 * 30)


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class LinkBody(BaseModel):
    url: str = Field(min_length=4, max_length=2048)


class CallAnswerBody(BaseModel):
    question_id: str = Field(max_length=10)
    transcript: str = Field(default="", max_length=8000)


def _card_fields(a: Approval) -> list[dict[str, str]]:
    """The facts the owner decides on, first: who gets it and what it is called."""
    p = a.payload or {}
    if a.kind != "gmail_send" or not p.get("to"):
        return []
    return [
        {"label": "To", "value": ", ".join(str(x) for x in p.get("to") or [])},
        {"label": "Subject", "value": str(p.get("subject") or "")},
    ]


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/app", dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)])

    def onboarder() -> Onboarder:
        agent = get_agent()  # its settings include credentials connected in the app
        return Onboarder(agent, telegram_username(agent.settings.telegram_bot_token))

    @router.get(
        "/agents",
        tags=["Marketplace & hiring"],
        summary="List agents",
        description="The agents on offer and whether each is available or already hired. Who hired it is never shown.",
    )
    def agents() -> list[dict[str, Any]]:
        settings = get_settings()
        with session_scope() as s:
            # Whether Ava is taken, never by whom: the owner's name is private. With many owners,
            # never: everyone can hire their own.
            hired = not settings.allow_many_owners and accounts.anyone_hired(s)
        result = []
        for a in catalog(settings):
            item = dict(a)
            if a["slug"] == "ava":
                item["hired"] = hired
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
                if settings.require_verified_email and user.email_verified_at is None:
                    raise HTTPException(403, "Please confirm your email address first: open the link we emailed you.")
                try:
                    person = accounts.hire(s, user, body.agent)
                except accounts.AlreadyHired as exc:
                    # Not the owner's name: any signed-up account can ask, and it's not theirs to know.
                    raise HTTPException(409, f"{settings.agent_name} already works for someone else.") from exc
                found = accounts.instance_of(s, user.id, body.agent)
                assert found is not None
                with use_instance(found[0]):  # the introduction starts inside the new instance
                    rescope(s)
                    onboarder().ensure_started(s, person)
                return {"hired": body.agent, "person": {"id": str(person.id), "name": person.display_name}}
        except IntegrityError as exc:  # someone else hired at the same moment
            raise HTTPException(409, f"{settings.agent_name} was just hired by someone else.") from exc

    @router.post(
        "/resume",
        tags=["Owner"],
        summary="Resume Ava",
        description=(
            "Lifts a pause that is the owner's own: the halt word, the circuit breaker or the action witness. "
            "A pause set by the server's operator (the console) answers 409; only the operator can lift that."
        ),
    )
    def resume(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            if platform_frozen(s):
                raise HTTPException(409, "The server's operator paused every Ava here. Only they can resume it.")
            if instance_frozen(s):
                set_frozen(s, False, actor="owner_resume")
            return {"paused": is_frozen(s)}

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
            # The channel's app/bot is connected (from the app or .env); its token is in the runtime settings either
            # way. A linked account only counts while the app/bot is there: with it disconnected, nothing reaches
            # the owner on that channel.
            telegram_on = bool(settings.telegram_bot_token)
            slack_on = slack_connected(settings)
            return {
                "person": {"id": str(person.id), "name": person.display_name},
                "agent": {"name": settings.agent_name, "role": "Personal assistant", "stage": "Infant"},
                "onboarding": {"step": state.step, "done": state.step == "done"},
                "brain": "claude" if brain_ready(settings) else "offline",
                "brain_provider": brain_status(settings).provider,
                "paused": is_frozen(s),
                # The owner can lift a pause that is hers (the halt word, the circuit breaker, the witness);
                # one the server's operator set is theirs to lift.
                "can_resume": instance_frozen(s) and not platform_frozen(s),
                # Which optional parts are on, so the app does not ask for what answers 404 by design.
                "features": {"triage": bool(settings.triage_enabled)},
                "channels": {
                    "web": True,
                    "telegram": "telegram" in providers and telegram_on,
                    # The bot is connected (the owner's own account may still be unlinked).
                    "telegram_available": telegram_on,
                    "slack": "slack" in providers and slack_on,
                    # The Slack app is connected (its account may still be unlinked), like telegram_available.
                    "slack_available": slack_on,
                    # Whether each service still accepts our token (stored, never fetched here): "Linked to
                    # you" on Home means nothing if Telegram rejects the bot token.
                    "health": {p: health.get(s, p) for p in health.PROVIDERS},
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
            view = ob.answer(s, require(s, Person, person_id), body.step, body.answer).as_dict()
        if body.step == "brain":
            from superaide.knowledge.pending import brain_became_ready

            brain_became_ready()  # after the commit: read anything already shared (issue #12)
        return view

    # --- introducing yourself with files, links or a video call -------------------

    def _said(person_id: uuid.UUID, line: str) -> None:
        with session_scope() as s:
            onboarder()._say(s, require(s, Person, person_id), [line])

    def _ingest_once(person_id: uuid.UUID, extracted: extract.Extracted, *, size_bytes: int) -> Any:
        """ingest(), where sharing the same file twice at the same moment (a double click, two tabs)
        is not an error: the request that lost the race hits the one-copy-per-owner index, and
        trying again finds the winner's copy and answers "you've already shared this"."""
        try:
            return ingest(get_agent(), person_id, extracted, size_bytes=size_bytes)
        except IntegrityError:
            return ingest(get_agent(), person_id, extracted, size_bytes=size_bytes)

    @router.post(
        "/intro/files",
        tags=["Introduction"],
        summary="Share a file",
        description=(
            "A resume or CV (PDF, Word, text; up to 10 MB). The text is kept and read into notebook facts; offline it"
            " is read once Claude is connected."
        ),
    )
    def intro_file(file: UploadFile = File(...), person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        # A plain `def`: FastAPI runs it in a worker thread, so reading a PDF and the model call
        # below don't freeze the event loop (and every other owner's request) while they work.
        data = file.file.read(extract.MAX_FILE_BYTES + 1)
        try:
            extracted = extract.extract_file(file.filename or "upload", data)
        except extract.ExtractError as exc:
            raise HTTPException(422, str(exc)) from exc
        _require_text(extracted)
        result = _ingest_once(person_id, extracted, size_bytes=len(data))
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
        result = _ingest_once(person_id, extracted, size_bytes=len(extracted.text))
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

    @router.post(
        "/intro/documents/{document_id}/retry",
        tags=["Introduction"],
        summary="Try reading a file or link again",
        description="For one that failed to be read: reads it again now and says how it went.",
    )
    def retry_document_route(document_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        from superaide.knowledge.ingest import retry_document

        result = retry_document(get_agent(), person_id, document_id)
        if result is None:
            raise HTTPException(404, "Not found")
        with session_scope() as s:
            doc = s.get(Document, document_id)
            status = doc.status if doc is not None else "removed"
        return {"status": status, "facts": result.facts, "read": result.read}

    @router.post(
        "/intro/documents/{document_id}/erase",
        tags=["Introduction"],
        summary="Erase a file or link for good",
        description="Deletes its text, its passages, what Ava learned only from it and their vectors. Unlike "
        "remove, this cannot be undone. A fact you also said yourself, or that another file supports, stays.",
    )
    def erase_document_route(document_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            doc = s.get(Document, document_id)
            if doc is None or doc.owner_person_id != person_id:
                raise HTTPException(404, "Not found")
            counts = erase_document(s, require(s, Person, person_id), doc)
        erase_vacuum()
        return {"status": "erased", **counts}

    @router.post(
        "/people/{contact_id}/erase",
        tags=["Owner: people & rules"],
        summary="Erase what Ava holds about a person",
        description="Deletes the notes they asked Ava to keep and notes about them, and empties the text of their "
        "messages. For a person on the owner's People list.",
    )
    def erase_person_route(contact_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            contact = s.get(Contact, contact_id)
            if contact is None or contact.owner_person_id != person_id or contact.removed_at is not None:
                raise HTTPException(404, "Not found")
            counts = erase_person(s, require(s, Person, person_id), contact.person_id)
        erase_vacuum()
        return {"status": "erased", **counts}

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
        description="The owner's conversation with Ava, oldest first (introduction included). Returns the newest "
        "`limit` messages (default and maximum 200). More older messages exist when the response has an "
        "`X-Next-Cursor` header (also `Link: rel=next`, and `X-Has-More: true`); send it back as `cursor` for "
        "the page before. `before` (the id of the oldest message you have) still works and means the same thing "
        "for a client that tracks its own anchor. ADR 0014.",
    )
    def messages(
        request: Request,
        response: Response,
        person_id: uuid.UUID = Depends(owner_person_id),
        limit: int = limit_param(200, 200),
        cursor: str | None = cursor_param(),
        before: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        response.headers["X-Has-More"] = "false"
        with session_scope() as s:
            ctx = s.scalar(select(Context).where(Context.provider == "web", Context.external_id == f"web:{person_id}"))
            if ctx is None:
                return []
            keys = [SortKey(Event.created_at), SortKey(Event.id)]
            bind = bind_for(person_id)
            if before is not None and cursor is None:
                anchor = s.get(Event, before)
                if anchor is None or anchor.context_id != ctx.id:  # someone else's, or gone: nothing older to give
                    return []
                cursor = encode_cursor([anchor.created_at, anchor.id], keys, endpoint="messages", bind=bind)
            page = paginate(
                s,
                select(Event).where(Event.context_id == ctx.id),
                keys,
                endpoint="messages",
                bind=bind,
                limit=limit,
                cursor=cursor,
                maximum=200,
                build=lambda e: {
                    "id": str(e.id),
                    "from": "you" if e.direction == "in" else "agent",
                    "text": e.content,
                    "at": e.created_at.isoformat(),
                    "kind": "onboarding" if (e.meta or {}).get("onboarding") else "chat",
                    "sources": (e.meta or {}).get("sources", []),
                    "activity": (e.meta or {}).get("activity", []),
                    "reasoning": (e.meta or {}).get("reasoning", ""),
                },
            )
            response.headers["X-Has-More"] = "true" if page.has_more else "false"
            set_next_headers(response, str(request.url), page.next_cursor)
            return list(reversed(page.items))

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

    @router.post(
        "/chat/stream",
        tags=["Owner: chat & notebook"],
        summary="Send a message and watch Ava work",
        description=(
            "Like `/chat`, but answers with Server-Sent Events as Ava works: `status`, `reasoning` "
            "(her summarised thinking), `narration` (what she says she's doing), `tool` (each skill or "
            "tool call, `running` then `done` or `error`), `text` (the answer as it's written), and "
            "finally `done` with the reply. The reply is saved even if the page is closed mid-way."
        ),
        response_class=StreamingResponse,
    )
    def chat_stream(body: ChatBody, person_id: uuid.UUID = Depends(owner_person_id)) -> StreamingResponse:
        text = body.text.strip()
        if not text:
            raise HTTPException(422, "Type a message first.")
        with session_scope() as s:
            person = require(s, Person, person_id)
            msg = accounts.web_message(person, text)
        return StreamingResponse(
            stream_turn(lambda emit: get_agent().handle(msg, emit=emit)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get(
        "/notebook",
        tags=["Owner: chat & notebook"],
        summary="Notebook",
        description="Everything Ava currently knows about the owner, newest first, with its category and source. "
        "Paged: `limit` (1 to 200, default 200) and `cursor` (the previous page's `next_cursor`); `has_more` says "
        "whether older notes exist, so a long notebook is never cut silently. `q` finds notes containing a word "
        "and `category` keeps one kind. ADR 0014.",
    )
    def notebook(
        person_id: uuid.UUID = Depends(owner_person_id),
        limit: int = limit_param(200, 200),
        cursor: str | None = cursor_param(),
        q: str = Query("", max_length=200),
        category: str = Query("", max_length=40),
    ) -> dict[str, Any]:
        """Everything the agent currently knows about the owner, newest first, with where it came from."""
        stmt: Any = (
            select(MemoryItem, Document.title)
            .outerjoin(Document, Document.id == MemoryItem.document_id)
            .where(
                MemoryItem.subject_person_ids.contains([person_id]),
                MemoryItem.valid_to.is_(None),
                MemoryItem.conflict_status == "none",  # claims waiting for a decision are under /notebook/conflicts
            )
        )
        if q.strip():
            word = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(MemoryItem.content.ilike(f"%{word}%", escape="\\"))
        if category:
            stmt = stmt.where(MemoryItem.category == category)
        with session_scope() as s:
            page = paginate(
                s,
                stmt,
                [SortKey(MemoryItem.created_at), SortKey(MemoryItem.id)],
                endpoint="notebook",
                bind=bind_for(person_id),
                limit=limit,
                cursor=cursor,
                maximum=200,
                entity_of=lambda row: row[0],
                build=lambda row: {
                    "id": str(row[0].id),
                    "text": row[0].content,
                    "category": row[0].category,
                    "source": row[0].source,
                    "document": row[1],
                    "passage": str(row[0].chunk_id) if row[0].chunk_id else None,
                    "shareable": row[0].allowed_kind == "public",
                    "private": row[0].allowed_kind == "persons",
                    "hearsay": row[0].belief_state == "hearsay",
                    "at": row[0].created_at.isoformat(),
                },
            )
            return {"memories": page.items, "next_cursor": page.next_cursor, "has_more": page.has_more}

    @router.get(
        "/notebook/conflicts",
        tags=["Owner: chat & notebook"],
        summary="Which is current?",
        description="Claims from a weaker source that disagree with what Ava knows, and link facts held for review. "
        "Neither is used until the owner decides.",
    )
    def notebook_conflicts(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            docs = {
                d.id: d.title or d.source
                for d in s.scalars(select(Document).where(Document.owner_person_id == person_id))
            }

            def card(m: MemoryItem) -> dict[str, Any]:
                return {
                    "id": str(m.id),
                    "text": m.content,
                    "category": m.category,
                    "source": m.source,
                    "document": docs.get(m.document_id) if m.document_id else None,
                    "at": m.created_at.isoformat(),
                }

            return {
                "conflicts": [
                    {"claim": card(claim), "current": card(current) if current else None}
                    for claim, current in conflicts_of(s, person_id)
                ]
            }

    @router.post(
        "/notebook/conflicts/{memory_id}/resolve",
        tags=["Owner: chat & notebook"],
        summary="Decide which is current",
        description="keep: the current fact stands. new: the claim is accepted. custom: the owner's own wording. "
        "The answer is stored as a top-trust fact; the older rows stay, retired.",
    )
    def resolve_notebook_conflict(
        memory_id: uuid.UUID, body: ResolveConflict, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, str]:
        with session_scope() as s:
            claim = s.get(MemoryItem, memory_id)
            if (
                claim is None
                or person_id not in (claim.subject_person_ids or [])
                or claim.conflict_status not in ("pending", "review")
                or claim.valid_to is not None
            ):
                raise HTTPException(404, "Not found in your notebook")
            if body.choice == "custom" and not body.text.strip():
                raise HTTPException(422, "Write what is true.")
            resolve_conflict(s, require(s, Person, person_id), claim, body.choice, body.text)
            return {"status": "resolved"}

    @router.post(
        "/notebook/sources/{source}/erase",
        tags=["Owner: chat & notebook"],
        summary="Erase what was learned from Gmail or the calendar",
        description="Deletes every short fact read from `gmail` or `calendar`, with its vectors. Cannot be undone.",
    )
    def erase_connector_facts_route(source: str, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        if source not in ("gmail", "calendar"):
            raise HTTPException(404, "Not found")
        from superaide.knowledge.connector_facts import erase_source

        with session_scope() as s:
            return {"status": "erased", "facts_deleted": erase_source(s, source)}

    @router.get(
        "/passages/{chunk_id}",
        tags=["Owner: chat & notebook"],
        summary="A cited passage",
        description="The exact passage of a shared file or page that a note or an answer cites (ava://chunk/<id>).",
    )
    def passage(chunk_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            chunk = s.get(DocumentChunk, chunk_id)
            doc = s.get(Document, chunk.document_id) if chunk else None
            if chunk is None or doc is None or doc.owner_person_id != person_id or doc.removed_at is not None:
                raise HTTPException(404, "Not found")
            return {
                "id": str(chunk.id),
                "ordinal": chunk.ordinal,
                "text": chunk.text,
                "document": {"id": str(doc.id), "title": doc.title or doc.source, "kind": doc.kind},
            }

    @router.get(
        "/people",
        tags=["Owner: people & rules"],
        summary="People who matter",
        description="The owner's contacts and who can always get through: VIPs first, then by name. Paged: `limit` "
        "(1 to 200, default 200) and `cursor` (the previous page's `next_cursor`); `has_more` says whether more "
        "people follow. ADR 0014.",
    )
    def people(
        person_id: uuid.UUID = Depends(owner_person_id),
        limit: int = limit_param(200, 200),
        cursor: str | None = cursor_param(),
    ) -> dict[str, Any]:
        """The people who matter to the owner, VIPs first, and where Ava can recognise them."""
        with session_scope() as s:
            owner = require(s, Person, person_id)
            stmt: Any = (
                select(Contact, Person)
                .join(Person, Person.id == Contact.person_id)
                .where(Contact.owner_person_id == owner.id, Contact.removed_at.is_(None))
            )
            page = paginate(
                s,
                stmt,
                [SortKey(Contact.vip), SortKey(Person.display_name, desc=False), SortKey(Contact.id, desc=False)],
                endpoint="people",
                bind=bind_for(person_id),
                limit=limit,
                cursor=cursor,
                maximum=200,
                entity_of=lambda row: SimpleNamespace(vip=row[0].vip, display_name=row[1].display_name, id=row[0].id),
                build=lambda row: row,
            )
            # One query for the whole page (it was one per contact).
            channels: dict[uuid.UUID, set[str]] = {}
            ids = [p.id for _, p in page.items]
            if ids:
                for pid, provider in s.execute(
                    select(ChannelIdentity.person_id, ChannelIdentity.provider).where(
                        ChannelIdentity.person_id.in_(ids), ChannelIdentity.lifecycle == "active"
                    )
                ):
                    if pid is not None:
                        channels.setdefault(pid, set()).add(provider)
            return {
                "people": [
                    {
                        "id": str(contact.id),
                        "name": person.display_name,
                        "relationship": contact.relationship,
                        "vip": contact.vip,
                        "channels": sorted(channels.get(person.id, set())),
                    }
                    for contact, person in page.items
                ],
                "next_cursor": page.next_cursor,
                "has_more": page.has_more,
            }

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

    def question_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        """The owner, once One Good Question is on (404 otherwise; sign-in and ownership are checked first)."""
        if not get_settings().one_good_question_enabled:
            raise HTTPException(404, "Not found")
        return person_id

    def _question_view(q: Any) -> dict[str, Any] | None:
        if q is None:
            return None
        return {"id": str(q.id), "topic": q.topic, "question": q.question, "status": q.status, "answer": q.answer}

    @router.get(
        "/question",
        tags=["Owner: chat & notebook"],
        summary="Today's question",
        description=(
            "One Good Question (ONE_GOOD_QUESTION_ENABLED, off by default; 404 while off): the one clarifying "
            "question Ava may ask today, if she has asked it."
        ),
    )
    def question_today(person_id: uuid.UUID = Depends(question_on)) -> dict[str, Any]:
        from superaide.trust import questions

        with session_scope() as s:
            owner = require(s, Person, person_id)
            return {"question": _question_view(questions.today(s, owner))}

    @router.post(
        "/question/ask",
        tags=["Owner: chat & notebook"],
        summary="Ask today's question",
        description="Today's question, chosen if the day's budget is unspent: at most one a day, or null.",
    )
    def question_ask(person_id: uuid.UUID = Depends(question_on)) -> dict[str, Any]:
        from superaide.trust import questions

        with session_scope() as s:
            owner = require(s, Person, person_id)
            return {"question": _question_view(questions.ask(s, owner))}

    @router.post("/question/{question_id}/answer", tags=["Owner: chat & notebook"], summary="Answer the question")
    def question_answer(
        question_id: uuid.UUID, body: AnswerQuestionBody, person_id: uuid.UUID = Depends(question_on)
    ) -> dict[str, Any]:
        from superaide.trust import questions

        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return {"question": _question_view(questions.answer(s, owner, question_id, body.answer))}
            except questions.QuestionError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    @router.post(
        "/question/{question_id}/skip",
        tags=["Owner: chat & notebook"],
        summary="Skip the question",
        description="`never` also stops Ava asking about that topic again.",
    )
    def question_skip(
        question_id: uuid.UUID, body: SkipQuestionBody, person_id: uuid.UUID = Depends(question_on)
    ) -> dict[str, Any]:
        from superaide.trust import questions

        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return {"question": _question_view(questions.skip(s, owner, question_id, never=body.never))}
            except questions.QuestionError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    @router.post(
        "/notebook/{memory_id}/confirm",
        tags=["Owner: chat & notebook"],
        summary="Confirm a note is true",
        description="Turns hearsay (heard from outside, fewer than two independent origins) into a fact.",
    )
    def confirm_note(memory_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        with session_scope() as s:
            m = s.get(MemoryItem, memory_id)
            if m is None or person_id not in (m.subject_person_ids or []) or m.valid_to is not None:
                raise HTTPException(404, "Not found in your notebook")
            from superaide.memory import echo

            echo.confirm(s, m)
        return {"status": "fact"}

    def why_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        """The owner, once "Why did you say that?" is on (404 otherwise; sign-in and ownership are checked first)."""
        if not get_settings().why_replay_enabled:
            raise HTTPException(404, "Not found")
        return person_id

    def _why_view(rows: list[tuple[Any, Any]], stopped: str | None = None) -> dict[str, Any]:
        return {
            "notes": [
                {"id": str(m.id), "text": m.content, "influence": i.influence, "changed_the_answer": i.changed}
                for m, i in rows
            ],
            "never_used": [str(m.id) for m, i in rows if not i.changed],
            "stopped": stopped,
        }

    @router.get(
        "/why/{event_id}",
        tags=["Owner: chat & notebook"],
        summary="Which notes changed this reply",
        description="The stored result of a replay (404 while WHY_REPLAY_ENABLED is off, or if it isn't your reply).",
    )
    def why_get(event_id: uuid.UUID, person_id: uuid.UUID = Depends(why_on)) -> dict[str, Any]:
        from superaide.trust import replay

        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return _why_view(replay.stored(s, owner, event_id))
            except replay.ReplayError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    @router.post(
        "/why/{event_id}",
        tags=["Owner: chat & notebook"],
        summary="Why did you say that?",
        description=(
            "Re-runs the answer with each of the top notes removed, on the small model inside the spend cap, and "
            "stores how far each removal moved it. At most REPLAY_MAX_ABLATIONS + 1 model calls."
        ),
    )
    def why_run(event_id: uuid.UUID, person_id: uuid.UUID = Depends(why_on)) -> dict[str, Any]:
        from superaide.trust import replay

        agent = get_agent()
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                run = replay.explain(agent, s, owner, event_id)
            except replay.ReplayError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            s.flush()
            return _why_view(replay.stored(s, owner, event_id), run.stopped)

    def safety_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_settings().relationship_safety_enabled:
            raise HTTPException(404, "Not found")
        return person_id

    @router.get(
        "/relationship-safety",
        tags=["Owner: people & rules"],
        summary="Heavy-conversation counter",
        description=(
            "RELATIONSHIP_SAFETY_ENABLED (off by default; 404 while off): whether Ava counts emotionally heavy turns "
            "(the kind only, never the words) and this week's count against the reach-out threshold and weekly cap."
        ),
    )
    def safety_state(person_id: uuid.UUID = Depends(safety_on)) -> dict[str, Any]:
        from superaide.trust import companion

        with session_scope() as s:
            owner = require(s, Person, person_id)
            return {"counter_on": companion.counter_on(s), **companion.trend(s, owner)}

    @router.post(
        "/relationship-safety",
        tags=["Owner: people & rules"],
        summary="Switch the counter on or off",
        description="Off also forgets what was counted. The fixed replies and the feelings filter stay on either way.",
    )
    def safety_switch(body: CounterBody, person_id: uuid.UUID = Depends(safety_on)) -> dict[str, Any]:
        from superaide.trust import companion

        with session_scope() as s:
            companion.set_counter(s, body.on)
            return {"counter_on": body.on}

    def ratchet_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_settings().skill_ratchet_enabled:
            raise HTTPException(404, "Not found")
        return person_id

    def _playbook_view(p: Any) -> dict[str, Any]:
        return {
            "id": str(p.id), "title": p.title, "status": p.status, "body": p.body, "drafted_body": p.drafted_body,
            "tools": p.tools, "retired_reason": p.retired_reason,
            "trials": {"with": p.used_n, "without": p.unused_n},
        }  # fmt: skip

    @router.get(
        "/playbooks",
        tags=["Owner: skills"],
        summary="Playbooks",
        description=(
            "Skill Ratchet (SKILL_RATCHET_ENABLED, off by default; 404 while off): the playbooks Ava drafted and the "
            "ones you switched on. Only a playbook you edited can be active."
        ),
    )
    def playbooks_list(person_id: uuid.UUID = Depends(ratchet_on)) -> list[dict[str, Any]]:
        from superaide.trust.models import Playbook

        with session_scope() as s:
            rows = s.scalars(
                select(Playbook).where(Playbook.owner_person_id == person_id).order_by(Playbook.created_at.desc())
            ).all()
            return [_playbook_view(p) for p in rows]

    @router.post("/playbooks", tags=["Owner: skills"], summary="Ask Ava to draft a playbook")
    def playbook_draft(body: DraftPlaybookBody, person_id: uuid.UUID = Depends(ratchet_on)) -> dict[str, Any]:
        from superaide.trust import ratchet

        agent = get_agent()
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return _playbook_view(ratchet.draft(agent, s, owner, body.task))
            except ratchet.RatchetError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    @router.post(
        "/playbooks/{playbook_id}/activate",
        tags=["Owner: skills"],
        summary="Switch a playbook on",
        description="Send the text as you want it. It must differ from Ava's draft: unedited drafts never go live.",
    )
    def playbook_activate(
        playbook_id: uuid.UUID, body: ActivatePlaybookBody, person_id: uuid.UUID = Depends(ratchet_on)
    ) -> dict[str, Any]:
        from superaide.trust import ratchet

        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return _playbook_view(ratchet.activate(s, owner, playbook_id, body.body))
            except ratchet.RatchetError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    @router.post("/playbooks/{playbook_id}/retire", tags=["Owner: skills"], summary="Retire a playbook")
    def playbook_retire(playbook_id: uuid.UUID, person_id: uuid.UUID = Depends(ratchet_on)) -> dict[str, Any]:
        from superaide.trust import ratchet

        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                return _playbook_view(ratchet.retire(s, owner, playbook_id))
            except ratchet.RatchetError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    @router.post(
        "/playbooks/{playbook_id}/outcome", tags=["Owner: skills"], summary="Record how much you edited the result"
    )
    def playbook_outcome(
        playbook_id: uuid.UUID, body: OutcomeBody, person_id: uuid.UUID = Depends(ratchet_on)
    ) -> dict[str, Any]:
        from superaide.trust import ratchet

        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                pb = ratchet._mine(s, owner, playbook_id)
                return _playbook_view(ratchet.record_outcome(s, pb, used=body.used, distance=body.distance))
            except ratchet.RatchetError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    def tuner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        if not get_settings().overnight_tuner_enabled:
            raise HTTPException(404, "Not found")
        return person_id

    def _version_view(v: Any) -> dict[str, Any]:
        return {
            "id": str(v.id), "key": v.key, "version": v.version, "status": v.status, "text": v.text,
            "base_score": v.base_score, "cand_score": v.cand_score, "held_out": v.held_out,
            "failed": (v.invariants or {}).get("failed", []), "rationale": v.rationale,
        }  # fmt: skip

    @router.get(
        "/tuner",
        tags=["Owner: people & rules"],
        summary="Prompt versions",
        description=(
            "Overnight Tuner (OVERNIGHT_TUNER_ENABLED, off by default; 404 while off): the triage prompt versions Ava "
            "proposed, what the grader scored, and which one is active. Nothing changes without your approval."
        ),
    )
    def tuner_list(person_id: uuid.UUID = Depends(tuner_on)) -> dict[str, Any]:
        from superaide.trust import tuner
        from superaide.trust.models import PromptVersion

        with session_scope() as s:
            rows = s.scalars(select(PromptVersion).order_by(PromptVersion.version.desc()).limit(30)).all()
            return {"current": tuner.active_text(s), "versions": [_version_view(v) for v in rows]}

    @router.post("/tuner/run", tags=["Owner: people & rules"], summary="Propose a better triage prompt")
    def tuner_run(person_id: uuid.UUID = Depends(tuner_on)) -> dict[str, Any]:
        from superaide.trust import tuner

        agent = get_agent()
        with session_scope() as s:
            try:
                return _version_view(tuner.propose(agent, s))
            except tuner.TunerError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    @router.post(
        "/tuner/{version_id}/{verdict}", tags=["Owner: people & rules"], summary="Approve or reject a proposal"
    )
    def tuner_decide(version_id: uuid.UUID, verdict: str, person_id: uuid.UUID = Depends(tuner_on)) -> dict[str, Any]:
        from superaide.trust import tuner

        if verdict not in {"approve", "reject"}:
            raise HTTPException(404, "Not found")
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                fn = tuner.approve if verdict == "approve" else tuner.reject
                return _version_view(fn(s, owner, version_id))
            except tuner.TunerError as exc:
                raise HTTPException(exc.status, str(exc)) from exc

    @router.post("/tuner/rollback", tags=["Owner: people & rules"], summary="Go back to the built-in prompt")
    def tuner_rollback(person_id: uuid.UUID = Depends(tuner_on)) -> dict[str, str]:
        from superaide.trust import tuner

        with session_scope() as s:
            tuner.roll_back(s, require(s, Person, person_id))
        return {"status": "built-in prompt"}

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
        "/notebook/{memory_id}/recheck",
        tags=["Owner: chat & notebook"],
        summary='Answer "is this still true?"',
        description="yes keeps the note and asks again later; no retires it; changed retires it and saves "
        "`new_content` in its place.",
    )
    def recheck(
        memory_id: uuid.UUID, body: RecheckBody, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        from superaide.memory import recheck as recheck_mod

        with session_scope() as s:
            owner = s.get(Person, person_id)
            try:
                m = recheck_mod.answer(s, owner, memory_id, body.answer, body.new_content)  # type: ignore[arg-type]
            except LookupError:
                raise HTTPException(404, "Not found in your notebook") from None
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            return {"status": body.answer, "id": str(m.id)}

    @router.get(
        "/skills",
        tags=["Owner: skills"],
        summary="Skills",
        description="Every skill Ava can have, whether it's installed, and the permissions each one needs.",
    )
    def skills(person_id: uuid.UUID = Depends(owner_person_id)) -> list[dict[str, Any]]:
        with session_scope() as s:
            return skills_view(s)

    @router.post(
        "/skills/{publisher}/{name}/install",
        tags=["Owner: skills"],
        summary="Install a skill",
        description="`accepted` must list exactly the permissions the skill needs (what the consent screen showed).",
    )
    def install_skill(
        publisher: str, name: str, body: InstallBody, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            try:
                skill_registry.install(s, f"{publisher}/{name}", body.accepted)
            except skill_registry.SkillUnavailable as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return next(v for v in skills_view(s) if v["id"] == f"{publisher}/{name}")

    @router.post(
        "/skills/{publisher}/{name}/remove",
        tags=["Owner: skills"],
        summary="Remove a skill",
        description="Ava stops using it straight away. Built-in essentials can't be removed.",
    )
    def remove_skill(publisher: str, name: str, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            try:
                skill_registry.remove(s, f"{publisher}/{name}")
            except skill_registry.SkillUnavailable as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return next(v for v in skills_view(s) if v["id"] == f"{publisher}/{name}")

    @router.post(
        "/skills/{publisher}/{name}/options",
        tags=["Owner: skills"],
        summary="Switch a skill setting on or off",
        description=(
            "For settings that give a skill more tools, such as Google Calendar's `actions` (add, move and decline "
            "events). Switching on needs `accepted` to list exactly the extra permissions the consent screen "
            "showed. For calendar actions, `google_url` is Google's consent page when Google still has to grant "
            "calendar.events; open it to finish."
        ),
    )
    def skill_option(
        publisher: str,
        name: str,
        body: OptionBody,
        request: Request,
        person_id: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        from superaide.skills import calendar

        skill_id = f"{publisher}/{name}"
        with session_scope() as s:
            try:
                skill_registry.set_option(s, skill_id, body.setting, on=body.on, accepted=body.accepted)
            except skill_registry.SkillUnavailable as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            card = next(v for v in skills_view(s) if v["id"] == skill_id)
            upgrade = skill_id == calendar.SKILL_ID and body.on and not calendar.can_write(s)
        if upgrade:
            from superaide.app.connections import begin_google, connect_origin

            origin = connect_origin(request)  # an address Google doesn't know gets a message, not a failed page
            try:
                card["google_url"] = begin_google(user_id, events=True, origin=origin, back="skills")
            except HTTPException:
                card["google_url"] = None  # no OAuth client yet: connect Google Calendar first
        return card

    def _reminder_view(i: Intention, tz: str) -> dict[str, Any]:
        return {
            "id": str(i.id),
            "kind": i.kind,
            "text": i.text,
            "due_at": i.due_at.isoformat(),
            "due_text": intentions.describe_due(i.due_at, tz),
            "status": i.status,
            "delivered_at": i.delivered_at.isoformat() if i.delivered_at else None,
            "source": i.source,
            "channel": i.channel,
        }

    @router.get(
        "/reminders",
        tags=["Owner: reminders"],
        summary="Reminders",
        description="Open reminders and follow-ups, soonest first. `?all=true` includes finished ones.",
    )
    def reminders(all: bool = False, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            tz = intentions.owner_zone(s, owner)
            rows = intentions.upcoming(s, owner, include_closed=all)
            return {"timezone": tz, "reminders": [_reminder_view(i, tz) for i in rows]}

    @router.post(
        "/reminders",
        tags=["Owner: reminders"],
        summary="Add a reminder",
        description='Give `due_at`, or put the time in `text` (for example "Call Priya tomorrow at 5").',
    )
    def add_reminder(body: ReminderBody, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            tz = intentions.owner_zone(s, owner)
            text, due = body.text, body.due_at
            if due is None:
                found = find_moment(text, datetime.now(UTC).astimezone(zone(tz)))
                if found is None:
                    raise HTTPException(422, 'Add a time, like "tomorrow at 9" or "in 2 hours".')
                due, rest = found
                text = clean_task(rest) or text
            elif due.tzinfo is None:
                due = due.replace(tzinfo=zone(tz))
            try:
                intention = intentions.create(s, owner, text=text, due_at=due, source="dashboard")
            except intentions.IntentionError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return _reminder_view(intention, tz)

    @router.post(
        "/reminders/{reminder_id}/snooze",
        tags=["Owner: reminders"],
        summary="Snooze",
        description="Bring it back `minutes` from now.",
    )
    def snooze_reminder(
        reminder_id: uuid.UUID, body: SnoozeBody, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                i = intentions.snooze(s, owner, reminder_id, minutes=body.minutes)
            except intentions.IntentionError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return _reminder_view(i, intentions.owner_zone(s, owner))

    @router.post(
        "/reminders/{reminder_id}/{verdict}",
        tags=["Owner: reminders"],
        summary="Mark done or cancel",
        description="`verdict` is `done` or `cancel`.",
    )
    def close_reminder(
        reminder_id: uuid.UUID, verdict: str, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        if verdict not in {"done", "cancel"}:
            raise HTTPException(404, "Not found")
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                i = intentions.finish(s, owner, reminder_id, status="done" if verdict == "done" else "cancelled")
            except intentions.IntentionError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return _reminder_view(i, intentions.owner_zone(s, owner))

    @router.get(
        "/approvals",
        tags=["Owner: approvals"],
        summary="Waiting for your OK",
        description="Actions Ava was asked to take that need your yes first, newest first, plus recent decisions.",
    )
    def approvals(person_id: uuid.UUID = Depends(owner_person_id)) -> list[dict[str, Any]]:
        braked = get_settings().rubber_stamp_brake_enabled
        with session_scope() as s:
            rows = list(s.scalars(select(Approval).order_by(Approval.created_at.desc()).limit(50)).all())
            names = {p.id: p.display_name for p in s.scalars(select(Person)).all()}
            state = brake.state(s) if braked else None
            if state is not None and state.engaged:  # a smaller batch: only the few oldest waiting cards
                waiting = [a for a in reversed(rows) if a.status == "pending"][: brake.PENDING_CAP]
                keep = {a.id for a in waiting}
                rows = [a for a in rows if a.status != "pending" or a.id in keep]
            return [
                {
                    "id": str(a.id),
                    "kind": a.kind,
                    "summary": (a.payload or {}).get("summary", ""),
                    "fields": _card_fields(a),  # To and Subject first, for an email
                    "warnings": list((a.payload or {}).get("warnings") or []),
                    "on_approval": kinds.on_approval(a.kind),  # what yes does, one line
                    "executes": kinds.has_executor(a.kind),  # False: approving will not do anything (#326)
                    "requested_by": names.get(a.requested_by_person_id) if a.requested_by_person_id else None,
                    # A card past its expiry can no longer be answered: show it as such, not as waiting (#291).
                    "status": "expired" if a.status == "pending" and expired(a) else a.status,
                    "result": (a.payload or {}).get("result"),
                    "notes": list((a.payload or {}).get("notes") or []),
                    "at": a.created_at.isoformat(),
                    "decided_at": a.decided_at.isoformat() if a.decided_at else None,
                    "needs_reason": bool(state and a.status == "pending" and brake.needs_reason(a.kind, state)),
                    **({"origin": "voice", "readback": gateway.readback(a)} if gateway.from_voice(a) else {}),
                }
                for a in rows
            ]

    @router.get(
        "/approvals/brake",
        tags=["Owner: approvals"],
        summary="How fast you have been approving",
        description=(
            "The Rubber-Stamp Brake (RUBBER_STAMP_BRAKE_ENABLED, off by default; 404 while off): your recent "
            "approval speed and refusals, and whether Ava is asking for a typed reason on high-risk cards."
        ),
    )
    def approval_brake(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        if not get_settings().rubber_stamp_brake_enabled:
            raise HTTPException(404, "Not found")
        with session_scope() as s:
            st = brake.state(s)
            return {
                "engaged": st.engaged,
                "decisions": st.decisions,
                "median_latency_s": st.median_latency_s,
                "reject_rate": st.reject_rate,
                "per_kind": st.per_kind,
                "fast_below_s": brake.FAST_MEDIAN_S,
                "pending_cap": brake.PENDING_CAP,
            }

    @router.get(
        "/twin",
        tags=["Owner: approvals"],
        summary="How well Ava can predict your approvals",
        description=(
            "Owner Twin (OWNER_TWIN_ENABLED, off by default; 404 while off): a predictor of your approve or reject "
            "choices, scored on your past decisions, with a calibration table and the gate it must pass."
        ),
    )
    def twin(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        if not get_settings().owner_twin_enabled:
            raise HTTPException(404, "Not found")
        from dataclasses import asdict

        from superaide.trust import twin as owner_twin

        with session_scope() as s:
            return asdict(owner_twin.calibration(s))

    @router.post(
        "/approvals/{approval_id}/{verdict}",
        tags=["Owner: approvals"],
        summary="Approve or reject",
        description="`verdict` is `approve` or `reject`. Each approval can be decided once.",
    )
    def decide_approval(
        approval_id: uuid.UUID,
        verdict: str,
        body: DecideBody | None = None,
        person_id: uuid.UUID = Depends(owner_person_id),
    ) -> dict[str, Any]:
        if verdict not in {"approve", "reject"}:
            raise HTTPException(404, "Not found")
        with session_scope() as s:
            approval = s.get(Approval, approval_id)
            if approval is None:
                raise HTTPException(404, "No such approval")
            owner = require(s, Person, person_id)
            if get_settings().rubber_stamp_brake_enabled and approval.status == "pending":
                held = brake.check(s, approval, approve=verdict == "approve", reason=body.reason if body else None)
                if held:
                    s.commit()  # keep the audit line that says why it was held
                    raise HTTPException(422, held)
            try:
                resolve(s, approval, approve=verdict == "approve", decided_by=owner.display_name, via="app")
            except ApprovalError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return {"status": approval.status, "result": (approval.payload or {}).get("result")}

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
            from superaide.channels.telegram import bot_id

            code = accounts.create_invite(
                s, require(s, Person, person_id), "telegram", endpoint=bot_id(settings.telegram_bot_token)
            )
        return {"available": True, "url": f"https://t.me/{username}?start={code}", "expires_minutes": 15}

    @router.post(
        "/channels/slack/invite",
        tags=["Connections"],
        summary="Link my Slack account",
        description=(
            "A one-time code (15 minutes) the owner sends to Ava in a direct message on Slack: `link <code>`. "
            "It works only through the Slack app that is connected now. 404 when no Slack app is connected."
        ),
    )
    def invite_slack(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        settings = get_agent().settings
        if not slack_connected(settings):
            raise HTTPException(404, "Connect Slack first.")
        endpoint = slack_key(settings.slack_team_id, settings.slack_app_token)
        with session_scope() as s:
            code = accounts.create_invite(s, require(s, Person, person_id), "slack", endpoint=endpoint)
        return {
            "code": code,
            "expires_minutes": accounts.INVITE_TTL_MINUTES,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=accounts.INVITE_TTL_MINUTES)).isoformat(),
            "instruction": f"Open a direct message with {settings.agent_name} in Slack and send: link {code}",
        }

    @router.get(
        "/access-log",
        tags=["Owner: people & rules"],
        summary="Who at Superaide looked",
        description=(
            "Every time a Superaide operator opened your Ava's people, conversations, memories or log (ADR 0009): "
            "when, what, and the reason they gave. Newest first, in pages: `limit` 1 to 200 (default 100); when "
            "older entries exist the response has `X-Next-Cursor`, `Link: rel=next` and `X-Has-More: true`."
        ),
    )
    def access_log(
        request: Request,
        response: Response,
        person_id: uuid.UUID = Depends(owner_person_id),
        limit: int = 100,
        cursor: str | None = cursor_param(),
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))  # clamped, as before, now at 200 instead of 500
        with session_scope() as s:
            page = paginate(
                s,
                select(AuditLog).where(AuditLog.actor == "operator", AuditLog.action == "operator_access"),
                [SortKey(AuditLog.created_at), SortKey(AuditLog.id)],
                endpoint="access-log",
                bind=bind_for(person_id),
                limit=limit,
                cursor=cursor,
                maximum=200,
                build=lambda r: {
                    "id": str(r.id),
                    "at": r.created_at.isoformat(),
                    "what": (r.detail or {}).get("path", ""),
                    "reason": (r.detail or {}).get("reason", ""),
                },
            )
        response.headers["X-Has-More"] = "true" if page.has_more else "false"
        set_next_headers(response, str(request.url), page.next_cursor)
        return page.items

    return router
