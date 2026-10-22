"""HTTP side of the "Ask my Ava" link.

Public (no account, `/p/ask/{handle}`):
- `GET` the page data and a signed form token, `POST` a request, `POST .../click` for "Get your own".
- With ASK_LINK_ENABLED off, every one answers 404 like a path that doesn't exist. An unknown, an
  unpublished and a revoked handle all answer that same 404.
- POSTs need the X-Requested-With header (a cross-site form can't send it), a token from the page
  (signed, bound to the handle, at least ASK_MIN_SECONDS old), and stay under per-address and
  per-handle limits. A filled honeypot or a too-fast send gets the normal confirmation and is dropped.

Owner (`/app/ask-link`): the link (create, change, publish, revoke, toggles) and the requests (reply,
dismiss, mark spam). Sign-in and CSRF are checked first, so others get 401/403 as on any owner route;
with the flag off the owner gets 404.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from superaide.app.routes import owner_person_id
from superaide.asklink import handles, service
from superaide.asklink.models import AskHandle, AskLink, AskRequest
from superaide.audit import audit
from superaide.auth.routes import client_of, reject_nul_text, require_csrf_header
from superaide.config import get_settings
from superaide.db.models import Approval, Person
from superaide.db.session import require, session_scope
from superaide.tenancy.context import scope_or_default, use_instance

MAX_BODY = 8 * 1024
LIMITS = {"name": 80, "contact": 200, "topic": 140, "why": 1500}


def _not_found() -> HTTPException:
    return HTTPException(404, "Not Found")  # the same body as a path that doesn't exist


def flag_on() -> None:
    if not service.enabled():
        raise _not_found()


async def small_body(request: Request) -> None:
    length = request.headers.get("content-length")
    if (length and length.isdigit() and int(length) > MAX_BODY) or len(await request.body()) > MAX_BODY:
        raise HTTPException(413, "That's too long. Shorten it and try again.")


class AskBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=LIMITS["name"])
    contact: str = Field(min_length=3, max_length=LIMITS["contact"])
    topic: str = Field(min_length=3, max_length=LIMITS["topic"])
    why: str = Field(default="", max_length=LIMITS["why"])
    deadline: date | None = None
    website: str = Field(default="", max_length=500)  # the honeypot: people never see it
    token: str = Field(default="", max_length=200)


class LinkBody(BaseModel):
    handle: str | None = Field(default=None, max_length=60)
    published: bool | None = None
    show_first_name: bool | None = None


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _confirmation() -> dict[str, Any]:
    return {"status": "sent", "message": service.CONFIRMATION, "get_own": service.GET_OWN}


def _invalid(message: str, field: str) -> HTTPException:
    return HTTPException(422, {"message": message, "field": field})


def make_public_router() -> APIRouter:
    router = APIRouter(prefix="/p/ask", tags=["Public: Ask my Ava"])

    def _find(handle: str) -> AskHandle:
        with session_scope() as s:
            row = service.published_handle(s, handle)
            if row is None:
                raise _not_found()
            s.expunge(row)
            return row

    @router.get(
        "/{handle}",
        summary="An Ask link's page",
        description="What the owner chose to show, the AI notice, and a form token. 404 unless published.",
        dependencies=[Depends(flag_on)],
    )
    def page(handle: str) -> dict[str, Any]:
        row = _find(handle)
        with use_instance(service.scope_of(row)), session_scope() as s:
            link = service.live_link(s, row.handle)
            if link is None:
                raise _not_found()
            line, first = service.display_line(s, link)
            service.count(s, "views", service.scope_of(row))
            return {
                "handle": row.handle,
                "display": line,
                "first_name": first,
                "notice": service.AI_NOTICE,
                "token": service.issue_token(row.id),
                "limits": LIMITS,
            }

    @router.post(
        "/{handle}",
        summary="Leave a request",
        description="No model reads it for you: code ranks it, and the owner decides. Same answer whatever happens.",
        dependencies=[Depends(flag_on), Depends(require_csrf_header), Depends(small_body), Depends(reject_nul_text)],
    )
    def ask(handle: str, body: AskBody, request: Request) -> dict[str, Any]:
        row = _find(handle)
        settings = get_settings()
        now = datetime.now(UTC)
        scope = service.scope_of(row)
        age = service.token_age(body.token, row.id)
        if age is None or age > settings.ask_token_max_age_s:
            raise HTTPException(400, "This form has expired. Reload the page and try again.")
        with use_instance(scope):
            with session_scope() as s:  # its own transaction, so every attempt counts
                limited = service.over_limit(s, ip=client_of(request).ip, handle_id=row.id, now=now)
            if limited:
                raise HTTPException(429, "Too many requests from here right now. Try again in an hour.")
            with session_scope() as s:
                link = service.live_link(s, row.handle)
                if link is None:
                    raise _not_found()
                if body.website.strip() or age < settings.ask_min_seconds:
                    service.count(s, "dropped", scope, now)
                    return _confirmation()
                name = service.clean(body.name)[: LIMITS["name"]]
                contact = service.clean(body.contact)[: LIMITS["contact"]]
                topic = service.clean(body.topic)[: LIMITS["topic"]]
                why = service.clean(body.why, multiline=True)[: LIMITS["why"]]
                if not name:
                    raise _invalid("Tell us your name.", "name")
                if len(topic) < 3:
                    raise _invalid("Say in a few words what it's about.", "topic")
                kind = service.contact_kind(contact)
                if kind is None:
                    raise _invalid("Give an email address or a phone number.", "contact")
                today = now.date()
                if body.deadline is not None and not today - timedelta(days=1) <= body.deadline <= today + timedelta(
                    days=366
                ):
                    raise _invalid("Pick a date in the coming year, or leave it empty.", "deadline")
                service.store_request(
                    s, link, name=name, contact=contact, kind=kind, topic=topic, why=why,
                    deadline=body.deadline, now=now,
                )  # fmt: skip
                service.count(s, "submissions", scope, now)
        return _confirmation()

    @router.post(
        "/{handle}/click",
        summary="Count a 'Get your own' click",
        description="A count only: nothing about the visitor is kept.",
        dependencies=[Depends(flag_on), Depends(require_csrf_header), Depends(small_body)],
    )
    def click(handle: str, request: Request) -> dict[str, bool]:
        row = _find(handle)
        now = datetime.now(UTC)
        with use_instance(service.scope_of(row)), session_scope() as s:
            bucket = f"click:{service.address_key(client_of(request).ip)}"
            if service.hit(s, bucket, timedelta(hours=1), now) <= 20:
                service.count(s, "get_own_clicks", service.scope_of(row), now)
        return {"ok": True}

    return router


def make_owner_router(get_agent: Callable[[], Any] | None = None) -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        """The owner, once the link is known to be on: others get 401/403 first, as everywhere."""
        if not service.enabled():
            raise _not_found()
        return person_id

    router = APIRouter(
        prefix="/app/ask-link",
        tags=["Owner: Ask my Ava link"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def _link(s: Any, person_id: uuid.UUID) -> AskLink | None:
        return s.scalar(
            select(AskLink).where(AskLink.owner_person_id == person_id, AskLink.agent_id == scope_or_default().agent_id)
        )

    def _view(s: Any, person_id: uuid.UUID) -> dict[str, Any]:
        link = _link(s, person_id)
        owner = require(s, Person, person_id)
        first = (owner.display_name.split() or [""])[0]
        out: dict[str, Any] = {
            "link": None,
            "suggested": handles.suggest(),
            "first_name": first,
            "notice": service.AI_NOTICE,
            "stats": service.totals(s, since=datetime.now(UTC).date() - timedelta(days=30)),
        }
        if link is not None:
            line, _ = service.display_line(s, link)
            out["link"] = {
                "handle": link.handle,
                "path": f"/a/{link.handle}",
                "published": link.published,
                "revoked": link.revoked_at is not None,
                "show_first_name": link.show_first_name,
                "display": line,
            }
        return out

    @router.get("", summary="Your Ask link", description="The link, what its page shows, and 30 days of counts.")
    def get_link(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return _view(s, person_id)

    def self_limit(scope: Any) -> None:
        """Every try at a new handle is counted (in its own transaction, so a refused try still counts): the
        answer "taken" or "yours" would otherwise let an owner list who holds which handle (SEC-10)."""
        with session_scope() as counting:
            tries = service.hit(counting, f"handle-try:{scope.agent_id}", timedelta(days=1), datetime.now(UTC))
        if tries > get_settings().ask_handle_tries_per_day:
            raise HTTPException(
                429, {"message": "Too many handles tried today. Try again tomorrow.", "field": "handle"}
            )

    @router.put(
        "",
        summary="Create or change your Ask link",
        description="Pick a handle, publish or unpublish, and choose whether the page shows your first name.",
    )
    def put_link(body: LinkBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        scope = scope_or_default()
        try:
            with session_scope() as s:
                link = _link(s, person_id)
                wanted = None
                if body.handle is not None:
                    try:
                        wanted = handles.check(body.handle)
                    except handles.HandleError as exc:
                        raise _invalid(str(exc), "handle") from exc
                if link is None and wanted is None:
                    raise _invalid("Pick a handle for your link.", "handle")
                if wanted is not None and (link is None or wanted != link.handle or link.revoked_at is not None):
                    self_limit(scope)
                    taken = s.scalar(select(AskHandle).where(AskHandle.handle == wanted))
                    if taken is not None and taken.agent_id != scope.agent_id:
                        raise HTTPException(409, {"message": "That handle is taken. Try another.", "field": "handle"})
                    if link is not None and link.handle != wanted:
                        _retire(s, link.handle, scope.agent_id)  # the old one stays reserved for you
                    if taken is None:
                        s.add(AskHandle(handle=wanted, tenant_id=scope.tenant_id, agent_id=scope.agent_id))
                        s.flush()
                    if link is None:
                        link = AskLink(owner_person_id=person_id, handle=wanted)
                        s.add(link)
                    link.handle = wanted
                    link.revoked_at = None
                    audit(s, "ask_link", "handle_set")
                assert link is not None
                if body.show_first_name is not None:
                    link.show_first_name = body.show_first_name
                if body.published is not None:
                    if body.published and link.revoked_at is not None:
                        raise HTTPException(409, {"message": "This link was revoked. Pick a handle to start again.",
                                                  "field": "handle"})  # fmt: skip
                    link.published = body.published
                    audit(s, "ask_link", "published" if body.published else "unpublished")
                s.flush()
                row = s.scalar(select(AskHandle).where(AskHandle.handle == link.handle))
                if row is not None and row.agent_id == scope.agent_id:
                    row.published = link.published and link.revoked_at is None
                s.flush()
                return _view(s, person_id)
        except IntegrityError as exc:  # two owners raced for the same handle
            raise HTTPException(409, {"message": "That handle is taken. Try another.", "field": "handle"}) from exc

    def _retire(s: Any, handle: str, agent_id: uuid.UUID) -> None:
        row = s.scalar(select(AskHandle).where(AskHandle.handle == handle, AskHandle.agent_id == agent_id))
        if row is not None:
            row.published = False

    @router.post(
        "/revoke",
        summary="Revoke your Ask link",
        description="The page stops working at once and looks like any unknown link. The handle stays yours.",
    )
    def revoke(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            link = _link(s, person_id)
            if link is None:
                raise HTTPException(404, "You don't have a link yet.")
            link.published = False
            link.revoked_at = datetime.now(UTC)
            _retire(s, link.handle, scope_or_default().agent_id)
            audit(s, "ask_link", "revoked")
            s.flush()
            return _view(s, person_id)

    @router.get(
        "/requests",
        summary="Requests from your link",
        description="Newest first. `status` is new (default), or all for the last 30 days.",
    )
    def requests(status: str = "new", person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return {"requests": open_requests(s, all_recent=status == "all")}

    def _request(s: Any, request_id: uuid.UUID) -> AskRequest:
        req = s.scalar(
            select(AskRequest).where(AskRequest.id == request_id, AskRequest.agent_id == scope_or_default().agent_id)
        )
        if req is None:
            raise HTTPException(404, "Not found")
        return req

    def _close(request_id: uuid.UUID, status: str) -> dict[str, Any]:
        with session_scope() as s:
            req = _request(s, request_id)
            if req.status == "spam":
                raise HTTPException(409, "This was already marked as spam.")
            req.status = status
            req.decided_at = datetime.now(UTC)
            if status == "spam":
                service.scrub(req)
            audit(s, "ask_link", f"request_{status}", request_id=req.id)
            s.flush()
            return service.request_view(req, _approval(s, req))

    @router.post("/requests/{request_id}/dismiss", summary="Dismiss a request")
    def dismiss(request_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        return _close(request_id, "dismissed")

    @router.post(
        "/requests/{request_id}/spam",
        summary="Mark a request as spam",
        description="What the sender typed is deleted; only a count is kept.",
    )
    def spam(request_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        return _close(request_id, "spam")

    @router.post(
        "/requests/{request_id}/reply",
        summary="Draft a reply",
        description="Your draft waits for your OK in Approvals. Nothing is sent to the visitor without it.",
    )
    def reply(request_id: uuid.UUID, body: ReplyBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            req = _request(s, request_id)
            if req.status == "spam":
                raise HTTPException(409, "This was marked as spam.")
            current = _approval(s, req)
            if current is not None and current.status == "pending":
                raise HTTPException(409, "A reply to this is already waiting for your OK.")
            text = service.clean(body.text, multiline=True)
            if not text:
                raise _invalid("Write a reply first.", "text")
            approval = Approval(
                kind="ask_reply",
                payload={
                    "summary": f"Reply to {req.name} about “{req.topic}”",
                    "request_id": str(req.id),
                    "text": text,
                },
                requested_by_person_id=person_id,
            )
            s.add(approval)
            s.flush()
            req.approval_id = approval.id
            audit(s, "ask_link", "reply_drafted", request_id=req.id, approval_id=approval.id)
            s.flush()
            return service.request_view(req, approval)

    return router


def _approval(s: Any, req: AskRequest) -> Approval | None:
    return s.get(Approval, req.approval_id) if req.approval_id else None


def open_requests(s: Any, *, all_recent: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    """The instance's requests, most pressing first among the new ones."""
    q = select(AskRequest).where(AskRequest.agent_id == scope_or_default().agent_id)
    if all_recent:
        q = q.where(AskRequest.created_at >= datetime.now(UTC) - timedelta(days=30))
    else:
        q = q.where(AskRequest.status == "new")
    rows = list(s.scalars(q.order_by(AskRequest.created_at.desc()).limit(limit)).all())
    order = {r: i for i, r in enumerate(service.RANKS)}
    if not all_recent:
        rows.sort(key=lambda r: order.get(r.rank, 9))  # stable: newest first within a rank
    return [service.request_view(r, _approval(s, r)) for r in rows]
