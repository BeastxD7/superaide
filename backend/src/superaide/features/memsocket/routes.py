"""HTTP side of the Memory Socket (#138).

`POST /mcp/memory` (Model Context Protocol, JSON-RPC 2.0 over HTTP, bearer token): `initialize`,
`tools/list` and `tools/call` for the single read-only tool `ask_ava_memory`. 404 while the flag is off; 401
for a missing, wrong or revoked token; 429 over the per-minute limit. No write tools exist.
Owner `/app/memory-socket`: add a consumer (the token is shown once), list them with the access log, revoke.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from superaide.app.deps import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.features.memsocket import service
from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.platform.tenancy.context import all_instances, use_instance

PROTOCOL = "2025-06-18"
TOOL = {
    "name": "ask_ava_memory",
    "description": "Ask the owner's assistant Ava's memory one question. Returns cited text only; read-only.",
    "inputSchema": {
        "type": "object",
        "properties": {"question": {"type": "string", "maxLength": service.MAX_QUESTION}},
        "required": ["question"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}
MAX_BODY = 8 * 1024


def _not_found() -> HTTPException:
    return HTTPException(404, "Not Found")


def _rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


class ClientBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=60)
    scope: str = Field(default="public", pattern="^(public|owner)$")


def make_public_router() -> APIRouter:
    router = APIRouter(prefix="/mcp", tags=["Memory Socket (MCP)"])

    @router.post(
        "/memory",
        summary="Read-only memory over MCP",
        description="JSON-RPC 2.0. One tool, ask_ava_memory(question). Bearer token per consumer.",
        response_model=None,
    )
    async def mcp(request: Request, authorization: str = Header(default="")) -> Response | dict[str, Any]:
        if not service.enabled():
            raise _not_found()
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "Too large")
        token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
        with all_instances(), session_scope() as s:
            row = service.find(s, token)
            if row is None:
                raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})
            client_id, scope = row.id, service.scope_of(row)
        if not service.within_limit(client_id):
            raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
        try:
            msg = await request.json()
        except ValueError:
            return _rpc_error(None, -32700, "Parse error")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _rpc_error(None, -32600, "Invalid request")
        rid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if rid is None:  # a notification (for example notifications/initialized): no reply
            return Response(status_code=202)
        if method == "initialize":
            asked = params.get("protocolVersion") if isinstance(params, dict) else None
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": asked if isinstance(asked, str) else PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "superaide-memory", "version": "1"},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}}
        if method == "tools/call":
            args = params.get("arguments") if isinstance(params, dict) else None
            question = args.get("question") if isinstance(args, dict) else None
            if not isinstance(params, dict) or params.get("name") != TOOL["name"] or not isinstance(question, str):
                return _rpc_error(rid, -32602, "Unknown tool or bad arguments")
            with use_instance(scope), session_scope() as s:
                client = s.get(service.MemorySocketClient, client_id)
                if client is None or client.revoked_at is not None:
                    raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})
                text = service.ask(s, client, question)
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}}
        return _rpc_error(rid, -32601, "Method not found")

    return router


def make_owner_router() -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        """The owner, once the socket is known to be on: others get 401/403 first, as everywhere."""
        if not service.enabled():
            raise _not_found()
        return person_id

    router = APIRouter(
        prefix="/app/memory-socket",
        tags=["Owner: memory socket"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    @router.get("", summary="My memory-socket consumers and their access log")
    def overview(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            log = s.scalars(
                select(AuditLog)
                .where(AuditLog.actor == "memory_socket", AuditLog.action == "query")
                .order_by(AuditLog.created_at.desc())
                .limit(100)
            ).all()
            return {
                "clients": [service.view(c) for c in service.clients(s)],
                "log": [
                    {
                        "at": r.created_at.isoformat(),
                        "client": (r.detail or {}).get("client", ""),
                        "scope": (r.detail or {}).get("scope", ""),
                        "returned": (r.detail or {}).get("returned", 0),
                    }
                    for r in log
                ],
            }

    @router.post("/clients", summary="Let another assistant read my memory (token shown once)")
    def add_client(body: ClientBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            row, token = service.create_client(s, name=body.name, scope=body.scope)
            return service.view(row) | {"token": token}

    @router.post("/clients/{client_id}/revoke", summary="Stop one assistant reading my memory")
    def revoke(client_id: uuid.UUID, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            if not service.revoke_client(s, client_id):
                raise HTTPException(404, "No such consumer")
            return {"revoked": True}

    return router
