"""/app/ambient: the owner's per-channel switch for reading whole channels (#509). Owner only, off by default.

GET lists the channels Ava is in (from Slack when it can be asked, plus the ones already known here) with each
switch. PUT turns one on or off; turning one ON needs `confirmed: true` (the app asks first and says what it means).
DELETE erases what was stored from one channel."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.channels import ambient, health
from superaide.channels.models import AmbientChannel
from superaide.channels.slack import scopes
from superaide.identity.auth.routes import reject_nul_text, require_csrf_header
from superaide.identity.vault.store import runtime_settings
from superaide.memory.models import Context
from superaide.platform.db.session import session_scope

log = logging.getLogger(__name__)

SLACK_LIST_URL = "https://slack.com/api/users.conversations"
MAX_CHANNELS = 200


class ChannelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="slack", max_length=20)
    channel_id: str = Field(min_length=2, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(default="", max_length=200)
    enabled: bool
    confirmed: bool = False  # the owner saw the explanation and said yes (required to turn one on)
    retention_days: int | None = None
    erase: bool = False  # when turning one off: also delete what was stored


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=10)


def _slack_channels(client: httpx.Client | None = None) -> tuple[list[dict[str, str]], str]:
    """The channels the Slack bot is a member of: [{"id", "name", "private"}], and an error word ("" when it worked)."""
    token = runtime_settings().slack_bot_token
    if not token:
        return [], "not_connected"
    out: list[dict[str, str]] = []
    cursor = ""
    try:
        http = client or http_client()
        for _ in range(5):
            params = {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            body = http.get(SLACK_LIST_URL, params=params, headers={"Authorization": f"Bearer {token}"}).json()
            if not isinstance(body, dict) or not body.get("ok"):
                return out, str(body.get("error") or "failed") if isinstance(body, dict) else "failed"
            for c in body.get("channels") or []:
                if c.get("id") and not c.get("is_im") and not c.get("is_mpim"):
                    out.append(
                        {
                            "id": str(c["id"]),
                            "name": str(c.get("name") or ""),
                            "private": str(bool(c.get("is_private"))),
                        }
                    )
            cursor = str((body.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor or len(out) >= MAX_CHANNELS:
                break
    except httpx.HTTPError, ValueError:
        return out, "unreachable"
    return out[:MAX_CHANNELS], ""


def _row_view(
    row: AmbientChannel | None, channel_id: str, title: str, stored: int, private: bool = False
) -> dict[str, Any]:
    return {
        "provider": "slack",
        "channel_id": channel_id,
        "title": title or (row.title if row else "") or channel_id,
        "private": private,
        "enabled": bool(row and row.enabled),
        "retention_days": row.retention_days if row else ambient.DEFAULT_RETENTION,
        "enabled_at": row.enabled_at.isoformat() if row and row.enabled_at else None,
        "last_heard_at": row.last_heard_at.isoformat() if row and row.last_heard_at else None,
        "paused": bool(row and row.paused_until),
        "stored": stored,
    }


def view(listed: list[dict[str, str]], listing_error: str) -> dict[str, Any]:
    with session_scope() as s:
        rows = {r.external_id: r for r in s.scalars(select(AmbientChannel).where(AmbientChannel.provider == "slack"))}
        known = {
            c.external_id: c.title
            for c in s.scalars(select(Context).where(Context.provider == "slack", Context.kind == "group"))
        }
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for c in listed:
            seen.add(c["id"])
            row = rows.get(c["id"])
            out.append(
                _row_view(row, c["id"], c["name"], ambient.stored_count(s, row) if row else 0, c["private"] == "True")
            )
        for cid in [*rows, *known]:
            if cid in seen:
                continue
            seen.add(cid)
            row = rows.get(cid)
            out.append(_row_view(row, cid, known.get(cid, ""), ambient.stored_count(s, row) if row else 0))
        out.sort(key=lambda r: (not r["enabled"], str(r["title"]).lower()))
        return {
            "channels": out,
            "listing_error": listing_error,
            "missing_scopes": [m for m in health.missing_scopes(s, "slack") if m in scopes.AMBIENT_SCOPES],
            "retention_choices": list(ambient.RETENTION_CHOICES),
            "default_retention": ambient.DEFAULT_RETENTION,
            "any_enabled": ambient.any_enabled(s),
        }


def make_router() -> APIRouter:
    router = APIRouter(
        prefix="/app/ambient",
        tags=["Owner: Connections"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "/channels",
        summary="Reading whole channels: your channels and their switches",
        description="The Slack channels Ava is in, each with its switch (off unless you turned it on), how long "
        "messages are kept, and how many are stored.",
    )
    def list_channels(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        listed, error = _slack_channels()
        return view(listed, error)

    @router.put(
        "/channels",
        summary="Reading whole channels: turn one channel on or off",
        description="Turning a channel on needs `confirmed: true`. Turning it off can also erase what was stored.",
    )
    def put_channel(body: ChannelBody, owner: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        if body.provider not in ambient.PROVIDERS:
            raise HTTPException(422, "Only Slack channels can be read whole.")
        if body.retention_days is not None and body.retention_days not in ambient.RETENTION_CHOICES:
            raise HTTPException(422, f"retention_days: choose one of {list(ambient.RETENTION_CHOICES)}")
        if body.enabled and not body.confirmed:
            raise HTTPException(409, "Confirm first: this lets Ava store every message of the channel.")
        team = runtime_settings().slack_team_id or ""
        with session_scope() as s:
            row = ambient.find(s, body.provider, body.channel_id)
            if row is None and not body.enabled:
                return {"ok": True, "erased": 0}
            if row is not None and not row.enabled and not body.enabled and not body.erase:
                return {"ok": True, "erased": 0}
            ctx_scope = s.scalar(
                select(Context.tenant_scope).where(
                    Context.provider == body.provider, Context.external_id == body.channel_id
                )
            )
            row = ambient.set_enabled(
                s,
                provider=body.provider,
                tenant_scope=(row.tenant_scope if row else ctx_scope or team),
                channel_id=body.channel_id,
                title=body.title,
                enabled=body.enabled,
                owner_id=owner,
                retention_days=body.retention_days,
            )
            erased = ambient.erase(s, row) if (body.erase and not body.enabled) else 0
        if body.enabled:  # a missing permission is an error and a notice, never a crash
            from superaide.app.connections import http_client as connections_client

            token = runtime_settings().slack_bot_token
            if token:
                scopes.validate_and_record(token, connections_client(), ambient=True)
        return {"ok": True, "erased": erased}

    @router.delete(
        "/channels/{channel_id}/messages",
        summary="Reading whole channels: erase what was stored from one channel",
        description="Deletes every message stored from this channel by reading it whole. Messages that mentioned "
        "Ava are not touched.",
    )
    def erase_channel(channel_id: str, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            row = s.scalar(
                select(AmbientChannel).where(
                    AmbientChannel.provider == "slack", AmbientChannel.external_id == channel_id
                )
            )
            if row is None:
                raise HTTPException(404, "No stored messages for this channel.")
            return {"ok": True, "erased": ambient.erase(s, row)}

    return router
