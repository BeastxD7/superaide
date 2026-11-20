"""/app/notion: connect the Notion capability (paste an integration secret), choose the pages Ava may see, and turn
drafts on. Issue #669.

Owner only, CSRF-checked, per instance. The token is checked against Notion (who it belongs to), then sealed in the
vault; responses carry only its hint and the integration's name, never the token. The reading limits live under
`/app/notion/read-settings`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.identity.auth.routes import current_user_id, reject_nul_text, require_csrf_header
from superaide.identity.vault import store
from superaide.integrations.notion import client as api
from superaide.integrations.notion import skill as notion
from superaide.platform.audit import audit
from superaide.platform.db.session import session_scope
from superaide.skills.connector_settings import last_changed
from superaide.skills.models import InstalledSkill


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=300)


class PagesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[str] = Field(max_length=notion.MAX_PAGES)


class WritesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _view(s: Any) -> dict[str, Any]:
    return {
        **notion.status(s),
        "last_changed": last_changed(s, "notion_settings", lambda d: [k for k in ("pages", "writes") if k in d]),
    }


def _row(s: Any) -> InstalledSkill:
    if notion.installed(s) is None:
        raise HTTPException(409, "Install the Notion capability first.")
    row = s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == notion.SKILL_ID))
    assert row is not None
    return row


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/notion",
        tags=["Owner: Notion"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Notion capability status", description="Installed, connected, pages and drafts.")
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return _view(s)

    @router.post(
        "/token",
        summary="Connect Notion with an integration secret",
        description=(
            "Checks the secret with Notion, then stores it encrypted. Create an internal integration in Notion, "
            "share only the pages you want Ava to see with it (read content; insert content and comments only if "
            "you will turn drafts on). The answer never contains the secret."
        ),
    )
    def connect(
        body: TokenBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        tok = body.token.strip()
        if not api.valid_token(tok):
            raise HTTPException(422, "That doesn't look like a Notion integration secret.")
        with session_scope() as s:
            _row(s)
        try:
            name = api.viewer(api.http_client(), tok)
        except api.NotionError as exc:
            raise HTTPException(400, str(exc)) from exc
        with session_scope() as s:
            store.save(s, "notion", {"token": tok}, user_id=user_id, meta={"login": name})
            return _view(s)

    @router.delete("/token", summary="Disconnect Notion", description="Deletes the stored integration secret.")
    def disconnect(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            store.remove(s, "notion", user_id=user_id)
            return _view(s)

    @router.put(
        "/pages",
        summary="Choose the pages Ava may see",
        description=(
            "Notion page links or ids, at most 20. Ava sees these pages and the pages beneath them, nothing else. "
            "Each change is audited."
        ),
    )
    def set_pages(body: PagesBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        raw = [r.strip() for r in body.pages if r.strip()]
        ids = [api.page_id(r) for r in raw]
        if any(i is None for i in ids):
            raise HTTPException(422, "Each page must be a link to a Notion page or its id.")
        wanted = list(dict.fromkeys(i for i in ids if i))
        with session_scope() as s:
            row = _row(s)
            row.settings = {**(row.settings or {}), "pages": wanted}
            audit(s, "skills", "notion_settings", pages=len(wanted))
            return _view(s)

    @router.post(
        "/writes",
        summary="Turn Notion drafts on or off",
        description="On: Ava may ask your approval to add a page or a comment. Nothing is written without your yes.",
    )
    def writes(body: WritesBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            row = _row(s)
            row.settings = {**(row.settings or {}), "writes": body.enabled}
            audit(s, "skills", "notion_settings", writes=body.enabled)
            return _view(s)

    return router
