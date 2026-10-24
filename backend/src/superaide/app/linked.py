"""/app/channels/linked: the chat accounts linked to people, which bot or app each was confirmed
on, and the owner's controls: confirm a new bot, unlink.

Signed-in owner only, and only their own Telegram and Slack accounts. A Telegram account that
wrote through a new bot is confirmed with a one-time
t.me link made here (only that account, only through that bot, 15 minutes); a Slack account on a new
app is confirmed with one tap here. Nothing in this module can be reached from a chat message.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.app import accounts
from superaide.app.deps import owner_person_id, telegram_username
from superaide.audit import audit
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.channels.base import bot_id
from superaide.db.models import ChannelIdentity, IdentityLinkEvent, Person
from superaide.db.session import require, session_scope
from superaide.identity import linking


def mask_account(identity: ChannelIdentity) -> str:
    """Enough to recognise your own account, not to look it up."""
    key = identity.stable_key
    return f"•••{key[-3:]}" if len(key) > 3 else "•••"


def endpoint_label(provider: str, key: str, current_bot: str, username: str | None) -> str:
    if not key:
        return "not confirmed on a bot yet" if provider == "telegram" else "not confirmed on an app yet"
    if provider == "telegram":
        return f"@{username}" if key == current_bot and username else f"bot {key}"
    team, _, app = key.partition("/")
    return f"app {app} in workspace {team}" if app else f"workspace {team}"


def _pending(identity: ChannelIdentity) -> dict[str, Any] | None:
    pending = (identity.verification or {}).get("pending")
    return pending if isinstance(pending, dict) and pending.get("endpoint") is not None else None


def _linked_at(session: Session, identity: ChannelIdentity) -> str | None:
    at = (identity.verification or {}).get("verified_at")
    if at:
        return str(at)
    event = session.scalar(
        select(IdentityLinkEvent)
        .where(IdentityLinkEvent.identity_id == identity.id, IdentityLinkEvent.action == "link")
        .order_by(IdentityLinkEvent.created_at.desc())
        .limit(1)
    )
    return (event.created_at if event else identity.created_at).isoformat()


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/channels/linked",
        tags=["Connections"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def current_bot() -> tuple[str, str | None]:
        token = get_agent().settings.telegram_bot_token
        return bot_id(token), telegram_username(token)

    def linked_identity(s: Session, identity_id: uuid.UUID, person_id: uuid.UUID) -> ChannelIdentity:
        """Only the signed-in owner's own Telegram or Slack accounts; anything else looks absent."""
        identity = s.get(ChannelIdentity, identity_id)
        if (
            identity is None
            or identity.person_id != person_id
            or identity.provider not in ("telegram", "slack")
            or identity.lifecycle != "active"
        ):
            raise HTTPException(404, "No such linked account")
        return identity

    @router.get(
        "",
        summary="Linked chat accounts",
        description=(
            "Your Telegram and Slack accounts (masked), the bot or app each was confirmed on, when it was linked "
            "and last seen, and any account waiting to be confirmed on a new bot or app."
        ),
    )
    def list_linked(person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        bot, username = current_bot()
        with session_scope() as s:
            rows = s.scalars(
                select(ChannelIdentity)
                .where(
                    ChannelIdentity.person_id == person_id,
                    ChannelIdentity.lifecycle == "active",
                    ChannelIdentity.provider.in_(("telegram", "slack")),
                )
                .order_by(ChannelIdentity.provider, ChannelIdentity.created_at)
            ).all()
            out = []
            for identity in rows:
                person = s.get(Person, identity.person_id)
                pending = _pending(identity)
                out.append(
                    {
                        "id": str(identity.id),
                        "provider": identity.provider,
                        "mine": identity.person_id == person_id,
                        "person": person.display_name if person else "",
                        "account": mask_account(identity),
                        "username": (identity.aux_handles or {}).get("username") or None,
                        "endpoint": endpoint_label(identity.provider, identity.endpoint_key, bot, username),
                        "linked_at": _linked_at(s, identity),
                        "last_seen_at": identity.last_seen_at.isoformat() if identity.last_seen_at else None,
                        # Messages through an unconfirmed bot are counted here, not in last_seen_at.
                        "last_seen_unconfirmed_at": pending.get("last_seen_at") if pending else None,
                        "pending": (
                            {
                                "reason": pending.get("reason", "new_endpoint"),
                                "endpoint": endpoint_label(identity.provider, str(pending["endpoint"]), bot, username),
                                "since": pending.get("since"),
                                # A Telegram link can only be made for the bot that is connected now.
                                "can_confirm": identity.provider == "slack"
                                or (pending["endpoint"] == bot and bool(username)),
                            }
                            if pending
                            else None
                        ),
                    }
                )
        return {"accounts": out}

    @router.post(
        "/{identity_id}/confirm",
        summary="Confirm an account on a new bot or app",
        description=(
            "Telegram: returns a one-time `t.me` link (15 minutes) that only this account can use, only through "
            "the bot it wrote to. Open it and press Start. Slack: confirms at once."
        ),
    )
    def confirm(identity_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        bot, username = current_bot()
        with session_scope() as s:
            identity = linked_identity(s, identity_id, person_id)
            pending = _pending(identity)
            if pending is None:
                raise HTTPException(409, "This account has nothing waiting to be confirmed.")
            endpoint = str(pending["endpoint"])
            person = require(s, Person, identity.person_id)
            if identity.provider == "telegram":
                if endpoint != bot or not username:
                    raise HTTPException(409, "That bot isn't the one connected now. Connect it first.")
                code = accounts.create_invite(s, person, "telegram", endpoint=endpoint, for_identity=identity)
                return {"url": f"https://t.me/{username}?start={code}", "expires_minutes": accounts.INVITE_TTL_MINUTES}
            # Slack has no start links; the signed-in owner's tap is the confirmation.
            previous = identity.endpoint_key
            identity.endpoint_key = endpoint
            identity.last_seen_at = datetime.now(UTC)  # confirmed: the silence clock starts again
            identity.verification = {"method": "confirmed_in_app", "verified_at": datetime.now(UTC).isoformat()}
            s.add(
                IdentityLinkEvent(
                    identity_id=identity.id, person_id=person.id, action="link", method="confirmed_in_app",
                    actor="app", evidence={"endpoint": endpoint, "previous_endpoint": previous},
                )
            )  # fmt: skip
            audit(s, "app", "endpoint_confirmed", identity_id=identity.id, endpoint=endpoint, by_person_id=person_id)
            return {"confirmed": True}

    @router.post(
        "/{identity_id}/unlink",
        summary="Unlink an account",
        description="Takes effect at once: the next message from that account is treated as from a stranger.",
    )
    def unlink(identity_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            identity = linked_identity(s, identity_id, person_id)
            linking.unlink(s, identity, actor="app", reason=f"unlinked in Connections by {person_id}")
        return {"unlinked": True}

    return router
