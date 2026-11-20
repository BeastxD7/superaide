"""/app/github: connect the GitHub capability (paste an access token), choose the repositories Ava may see, and turn
drafts on. Issue #669.

Owner only, CSRF-checked, per instance. The token is checked against GitHub (who it belongs to), then sealed in the
vault; responses carry only its hint and the login, never the token. The reading limits live under
`/app/github/read-settings`.
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
from superaide.integrations.github import client as api
from superaide.integrations.github import skill as github
from superaide.platform.audit import audit
from superaide.platform.db.session import session_scope
from superaide.skills.connector_settings import last_changed
from superaide.skills.models import InstalledSkill


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=300)


class ReposBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repos: list[str] = Field(max_length=github.MAX_REPOS)


class WritesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _view(s: Any) -> dict[str, Any]:
    return {
        **github.status(s),
        "last_changed": last_changed(s, "github_settings", lambda d: [k for k in ("repos", "writes") if k in d]),
    }


def _row(s: Any) -> InstalledSkill:
    if github.installed(s) is None:
        raise HTTPException(409, "Install the GitHub capability first.")
    row = s.scalar(select(InstalledSkill).where(InstalledSkill.skill_id == github.SKILL_ID))
    assert row is not None
    return row


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    router = APIRouter(
        prefix="/app/github",
        tags=["Owner: GitHub"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="GitHub capability status", description="Installed, connected, repositories and drafts.")
    def status(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return _view(s)

    @router.post(
        "/token",
        summary="Connect GitHub with an access token",
        description=(
            "Checks the token with GitHub, then stores it encrypted. Use a fine-grained token limited to the "
            "repositories you want Ava to see (read access to issues and pull requests; write access to issues only "
            "if you will turn drafts on). The answer never contains the token."
        ),
    )
    def connect(
        body: TokenBody, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        tok = body.token.strip()
        if not api.valid_token(tok):
            raise HTTPException(422, "That doesn't look like a GitHub access token.")
        with session_scope() as s:
            _row(s)
        try:
            login = api.viewer(api.http_client(), tok)
        except api.GithubError as exc:
            raise HTTPException(400, str(exc)) from exc
        with session_scope() as s:
            store.save(s, "github", {"token": tok}, user_id=user_id, meta={"login": login})
            return _view(s)

    @router.delete("/token", summary="Disconnect GitHub", description="Deletes the stored access token.")
    def disconnect(
        _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            store.remove(s, "github", user_id=user_id)
            return _view(s)

    @router.put(
        "/repos",
        summary="Choose the repositories Ava may see",
        description="owner/name, at most 20. Ava sees nothing outside this list. Each change is audited.",
    )
    def set_repos(body: ReposBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        wanted = list(dict.fromkeys(r.strip() for r in body.repos if r.strip()))
        bad = [r for r in wanted if not api.valid_repo(r)]
        if bad:
            raise HTTPException(422, "Each repository must look like owner/name.")
        with session_scope() as s:
            row = _row(s)
            row.settings = {**(row.settings or {}), "repos": wanted}
            audit(s, "skills", "github_settings", repos=len(wanted))
            return _view(s)

    @router.post(
        "/writes",
        summary="Turn GitHub drafts on or off",
        description="On: Ava may ask your approval to open an issue or comment. Nothing is written without your yes.",
    )
    def writes(body: WritesBody, _: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            row = _row(s)
            row.settings = {**(row.settings or {}), "writes": body.enabled}
            audit(s, "skills", "github_settings", writes=body.enabled)
            return _view(s)

    return router
