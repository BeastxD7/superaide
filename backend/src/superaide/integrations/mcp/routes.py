"""/app/mcp: the owner's MCP servers in Settings (issue #666).

Owner only, CSRF-checked, per instance (the signed-in account's own Ava). A response never carries a secret, only the
names and masked hints of what is stored in the vault. Adding a server that runs a program on this computer needs a
step-up confirmation (POST /app/stepup with action `mcp_stdio_server`, then send `confirmation`).
At most 20 servers per owner, so the list is bounded by product rule (ADR 0014) and is not paginated.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from superaide.app.deps import owner_person_id
from superaide.identity.auth import stepup
from superaide.identity.auth.models import UserAccount
from superaide.identity.auth.routes import current_user_id, reject_nul_text, require_csrf_header, session_token
from superaide.integrations.mcp import service, transport
from superaide.integrations.mcp.models import McpServer
from superaide.platform.db.session import session_scope


class AddBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    transport: Literal["stdio", "http"]
    command: str = Field(default="", max_length=300)
    args: list[str] = Field(default_factory=list, max_length=service.MAX_ARGS)
    url: str = Field(default="", max_length=500)
    env: dict[str, str] = Field(default_factory=dict)  # stdio: variables for the program, kept in the vault
    headers: dict[str, str] = Field(default_factory=dict)  # http: headers (an API key), kept in the vault
    confirmation: str | None = None  # the single-use nonce from POST /app/stepup, for a stdio server
    confirm_password: str | None = None


class ChangeBody(BaseModel):
    enabled: bool | None = None
    timeout_s: int | None = None
    calls_per_hour: int | None = None


class ApproveBody(BaseModel):
    tools: list[str] | None = Field(default=None, max_length=200)  # None: every tool of the last listing


class RiskBody(BaseModel):
    risk: Literal["read", "write", "destructive"]


def _server(s: Any, server_id: uuid.UUID) -> McpServer:
    server = service.get(s, server_id)
    if server is None:
        raise HTTPException(404, "No such server.")
    return server


def make_router() -> APIRouter:
    router = APIRouter(
        prefix="/app/mcp",
        tags=["Owner: MCP servers"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get(
        "/servers",
        summary="Your MCP servers",
        description="Every server with its tools, each tool's risk class and approval state. Secrets are never shown.",
    )
    def servers(_: uuid.UUID = Depends(owner_person_id)) -> dict[str, Any]:
        with session_scope() as s:
            return {
                "servers": [service.view(s, v) for v in service.all_servers(s)],
                "max_servers": service.MAX_SERVERS,
                "can_run_programs": not service.hosted(),
            }

    @router.post("/servers", status_code=201, summary="Add an MCP server")
    def add(
        body: AddBody,
        request: Request,
        _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        with session_scope() as s:
            if body.transport == "stdio":
                if service.hosted():  # no step-up can make this safe here
                    raise HTTPException(422, "Servers that run a program are not available on this hosted server.")
                user = s.get(UserAccount, user_id)
                if user is None:
                    raise HTTPException(403, "Confirm this change first.")
                try:
                    stepup.consume(
                        s, user=user, session_token=session_token(request), action="mcp_stdio_server",
                        nonce=body.confirmation, password=body.confirm_password,
                    )  # fmt: skip
                except stepup.StepUpError as exc:
                    s.commit()  # keep the audit line that says why it was refused
                    raise HTTPException(403, str(exc)) from exc
            try:
                server = service.create(
                    s, label=body.label, kind=body.transport, command=body.command, args=body.args, url=body.url,
                    env=body.env, headers=body.headers, user_id=user_id,
                )  # fmt: skip
            except service.Refused as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return service.view(s, server)

    @router.patch("/servers/{server_id}", summary="Switch a server on or off, or change its limits")
    def change(
        server_id: uuid.UUID,
        body: ChangeBody,
        _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        with session_scope() as s:
            server = _server(s, server_id)
            try:
                service.update(
                    s, server, enabled=body.enabled, timeout_s=body.timeout_s, calls_per_hour=body.calls_per_hour,
                    user_id=user_id,
                )  # fmt: skip
            except service.Refused as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return service.view(s, server)

    @router.delete("/servers/{server_id}", status_code=204, summary="Remove a server, its approvals and its secrets")
    def delete(
        server_id: uuid.UUID, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> None:
        with session_scope() as s:
            service.remove(s, _server(s, server_id), user_id=user_id)

    @router.post(
        "/servers/{server_id}/test",
        summary="Connect, list the tools and keep the listing",
        description="Nothing is callable by Ava until you approve the tools. A failure is returned as `error` (200).",
    )
    def test(
        server_id: uuid.UUID, _: uuid.UUID = Depends(owner_person_id), user_id: uuid.UUID = Depends(current_user_id)
    ) -> dict[str, Any]:
        with session_scope() as s:
            server = _server(s, server_id)
            error = ""
            try:
                service.refresh(s, server, user_id=user_id)
            except transport.McpError as exc:
                error = str(exc)
            return {**service.view(s, server), "error": error}

    @router.post(
        "/servers/{server_id}/approve",
        summary="Approve tools as listed",
        description="Records each tool's name, description, schema and hints. If the server later changes one, that "
        "tool is blocked until you approve it again.",
    )
    def approve(
        server_id: uuid.UUID,
        body: ApproveBody,
        _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        with session_scope() as s:
            server = _server(s, server_id)
            try:
                service.approve(s, server, body.tools, user_id=user_id)
            except service.Refused as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return service.view(s, server)

    @router.put(
        "/servers/{server_id}/tools/{tool}/risk",
        summary="Set a tool's risk class",
        description="read runs at once; write and destructive wait for your approval every time. Unset means write.",
    )
    def risk(
        server_id: uuid.UUID,
        tool: str,
        body: RiskBody,
        _: uuid.UUID = Depends(owner_person_id),
        user_id: uuid.UUID = Depends(current_user_id),
    ) -> dict[str, Any]:
        with session_scope() as s:
            server = _server(s, server_id)
            try:
                service.set_risk(s, server, tool, body.risk, user_id=user_id)
            except service.Refused as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            return service.view(s, server)

    return router
