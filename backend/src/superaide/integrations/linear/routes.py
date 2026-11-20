"""/app/linear: connect the Linear capability (paste an API key), choose the teams Ava may see, and turn
drafts on. Issue #669.

Owner only, CSRF-checked, per instance. The token is checked against Linear (who it belongs to), then sealed in the
vault; responses carry only its hint and the name, never the token. The reading limits live under
`/app/linear/read-settings`.
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
from superaide.integrations.linear import client as api
from superaide.integrations.linear import skill as linear
from superaide.platform.audit import audit
from superaide.platform.db.session import session_scope
from superaide.skills.connector_settings import last_changed
from superaide.skills.models import InstalledSkill


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=300)


class TeamsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teams: list[str] = Field(max_length=linear.MAX_TEAMS)


class WritesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _view(s: Any) -> dict[str, Any]:
    return {
        **linear.status(s),
        "last_changed": last_changed(s, "linear_settings", lambda d: [k for k in ("teams", "writes") if k in d]),
    }


def _row(s: Any) -> InstalledSkill:
    if linear.installed(s) is None:
        raise HTTPException(409, "Install the Linear capability first.")
    row = s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == linear.SKILL_ID))
    assert row is not None
    return row


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/linear",
        tags=["Owner: Linear"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="Linear capability status", description="Installed, connected, teams and drafts.")
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return _view(s)

    @router.post(
        "/token",
        summary="Connect Linear with an API key",
        description=(
            "Checks the key with Linear, then stores it encrypted. Create a personal API key in Linear (Settings, "
            "Security and access); it can do whatever you can, so Ava's reach is limited here by the teams you choose "
            "and by your approval of every draft. The answer never contains the key."
        ),
    )
    def connect(
        body: TokenBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        tok = body.token.strip()
        if not api.valid_token(tok):
            raise HTTPException(422, "That doesn't look like a Linear API key.")
        with session_scope() as s:
            _row(s)
        try:
            name = api.viewer(api.http_client(), tok)
        except api.LinearError as exc:
            raise HTTPException(400, str(exc)) from exc
        with session_scope() as s:
            store.save(s, "linear", {"token": tok}, user_id=user_id, meta={"login": name})
            return _view(s)

    @router.delete("/token", summary="Disconnect Linear", description="Deletes the stored API key.")
    def disconnect(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            store.remove(s, "linear", user_id=user_id)
            return _view(s)

    @router.put(
        "/teams",
        summary="Choose the teams Ava may see",
        description=(
            "Linear team keys such as ENG, at most 20. Ava sees issues in these teams, nothing else. "
            "Each change is audited."
        ),
    )
    def set_teams(body: TeamsBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        raw = [r.strip() for r in body.teams if r.strip()]
        keys = [api.team_key(r) for r in raw]
        if any(k is None for k in keys):
            raise HTTPException(422, "Each team must be its Linear key, such as ENG.")
        wanted = list(dict.fromkeys(k for k in keys if k))
        with session_scope() as s:
            row = _row(s)
            row.settings = {**(row.settings or {}), "teams": wanted}
            audit(s, "skills", "linear_settings", teams=len(wanted))
            return _view(s)

    @router.post(
        "/writes",
        summary="Turn Linear drafts on or off",
        description="On: Ava may ask your approval to open an issue or comment. Nothing is written without your yes.",
    )
    def writes(body: WritesBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            row = _row(s)
            row.settings = {**(row.settings or {}), "writes": body.enabled}
            audit(s, "skills", "linear_settings", writes=body.enabled)
            return _view(s)

    return router
