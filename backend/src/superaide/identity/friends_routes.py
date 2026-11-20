"""The owner's side of friends (#479, #504): accounts listed for a contact, the ones waiting for approval, invites.

Owner only. Approving is the only way an unverified account becomes a verified friend; it comes from the signed-in
owner, never from a chat. The platform's immutable identifier is what the owner is shown; the username and display
name are labelled as ones the sender chose. No model is involved."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from superaide.app.deps import owner_person_id, telegram_username
from superaide.identity import friends
from superaide.identity.auth.deps import reject_nul_text, require_csrf_header
from superaide.identity.models import ContactHandle, Person
from superaide.memory.models import Contact
from superaide.platform.db.session import require, session_scope


class HandleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(max_length=20)
    handle: str = Field(min_length=1, max_length=200)


def _contact(s: Any, owner_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    contact = s.get(Contact, contact_id)
    if contact is None or contact.owner_person_id != owner_id or contact.removed_at is not None:
        raise HTTPException(404, "Not found")
    return contact


def handles_of(s: Any, contact_id: uuid.UUID) -> list[dict[str, str]]:
    rows = s.query(ContactHandle).filter(ContactHandle.contact_id == contact_id).order_by(ContactHandle.created_at)
    return [{"id": str(h.id), "provider": h.provider, "kind": h.kind, "value": h.value} for h in rows]


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app",
        tags=["Owner: people & rules"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "/friends/pending",
        summary="Accounts waiting for your approval",
        description="Accounts acknowledged as a contact's (a username you listed, or an invite you made) but not "
        "trusted yet. Each shows the platform's immutable id; the username and name are ones the sender chose.",
    )
    def pending(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return {"pending": friends.pending_for(s, require(s, Person, person_id))}

    def decide(person_id: uuid.UUID, identity_id: uuid.UUID, verdict: str) -> dict[str, str]:
        with session_scope() as s:
            owner = require(s, Person, person_id)
            try:
                (friends.approve if verdict == "approve" else friends.decline)(s, owner, identity_id)
            except friends.FriendError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
        return {"status": "approved" if verdict == "approve" else "declined"}

    @router.post(
        "/friends/{identity_id}/approve",
        summary="Approve an account as this contact",
        description="Links the account to the contact. From then on Ava recognises it, greets them by name and may "
        "share what you allow for them.",
    )
    def approve(identity_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        return decide(person_id, identity_id, "approve")

    @router.post(
        "/friends/{identity_id}/decline",
        summary="Decline an account",
        description="Keeps the account a stranger and stops asking about it.",
    )
    def decline(identity_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, str]:
        return decide(person_id, identity_id, "decline")

    @router.post(
        "/people/{contact_id}/handles",
        summary="List an account for a contact",
        description="A Telegram username or id, or a Slack member id. A username only makes a sender an unverified "
        "friend you must approve; an id you type is your own approval.",
    )
    def add_handle(
        contact_id: uuid.UUID, body: HandleBody, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            contact = _contact(s, person_id, contact_id)
            try:
                friends.add_handle(s, contact, body.provider, body.handle)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            return {"handles": handles_of(s, contact.id)}

    @router.post(
        "/people/{contact_id}/handles/{handle_id}/remove",
        summary="Stop listing an account for a contact",
        description="Removes the listed account. An account already approved stays linked until it is unlinked.",
    )
    def remove_handle(
        contact_id: uuid.UUID, handle_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            contact = _contact(s, person_id, contact_id)
            h = s.get(ContactHandle, handle_id)
            if h is None or h.contact_id != contact.id:
                raise HTTPException(404, "Not found")
            s.delete(h)
            s.flush()
            return {"handles": handles_of(s, contact.id)}

    @router.post(
        "/people/{contact_id}/invite",
        summary="A one-time invite link for a contact",
        description="A Telegram link for this contact, valid a week and only through this bot. Whoever opens it is "
        "acknowledged as an unverified friend and waits for your approval. `available` is false until Telegram is "
        "connected.",
    )
    def invite(contact_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        settings = get_agent().settings
        username = telegram_username(settings.telegram_bot_token)
        if not username:
            with session_scope() as s:
                _contact(s, person_id, contact_id)
            return {"available": False}
        from superaide.channels.base import bot_id

        with session_scope() as s:
            contact = _contact(s, person_id, contact_id)
            code = friends.create_invite(s, contact, endpoint=bot_id(settings.telegram_bot_token))
        return {
            "available": True,
            "url": f"https://t.me/{username}?start={code}",
            "expires_days": int(friends.INVITE_TTL.days),
        }

    return router
