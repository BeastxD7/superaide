"""The owner's MCP server registry: add, list, enable, test, approve and classify tools, remove (issue #666).

Everything here belongs to the instance in scope (a hired Ava). A server is never read, listed or called under another
instance's scope: the table has Row-Level Security, and every query also filters on `agent_id`.

Secrets (environment variables for a stdio server, headers for a remote one) are sealed in the vault as one blob per
server and are shown only as masked hints. The model never sees a server's definition, only the tools the owner
approved.

A tool can be called only when (1) the owner approved it (`mcp/pin.py`: its name, description, schema and annotations
are hashed), and (2) the server still describes it exactly so at the moment of the call. A server that changes a tool
afterwards gets that tool blocked until the owner approves it again. The risk class (read, write, destructive) is the
owner's, bound to the same hash; unknown means write, and nothing the server says (`readOnlyHint`) lowers it.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from superaide.identity.auth.models import IntegrationSecret
from superaide.identity.vault import store
from superaide.identity.vault.crypto import mask
from superaide.integrations.mcp import transport
from superaide.integrations.mcp.models import McpServer
from superaide.mcp.api import pin_tools, tool_hash
from superaide.mcp.models import McpPin
from superaide.platform.audit import audit
from superaide.platform.db.models import AuditLog
from superaide.platform.security import urls
from superaide.platform.tenancy.context import scope_or_default

PIN_SKILL = "mcp"  # `McpPin.skill_id` for everything here; `McpPin.server` is the server's id
VAULT_PROVIDER = "mcp"
RISKS = ("read", "write", "destructive")
DEFAULT_RISK = "write"  # unknown is write: it asks the owner every time
MAX_SERVERS = 20
MAX_SECRETS = 20
MAX_ARGS = 20
MAX_SCHEMA_CHARS = transport.MAX_SCHEMA_CHARS
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,31}$")


class Refused(Exception):
    """A change that is not allowed, with a reason the owner can read. Routes answer 422 (409 for a clash)."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _mine() -> Any:
    return McpServer.agent_id == scope_or_default().agent_id


def hosted() -> bool:
    return urls.hosted()


def slugify(label: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:24].strip("-")
    return base or "server"


def get(session: Session, server_id: uuid.UUID) -> McpServer | None:
    return session.scalar(select(McpServer).where(_mine(), McpServer.id == server_id))


def all_servers(session: Session) -> list[McpServer]:
    return list(session.scalars(select(McpServer).where(_mine()).order_by(McpServer.created_at, McpServer.id)))


# --- secrets ------------------------------------------------------------------------------------------------


def _slot(server: McpServer) -> str:
    return server.id.hex  # IntegrationSecret.name is 40 characters


def _blob(session: Session, server: McpServer) -> dict[str, dict[str, str]]:
    row = store.find(session, VAULT_PROVIDER, _slot(server))
    if row is None:
        return {"env": {}, "headers": {}}
    data = json.loads(store.unseal(row))
    return {"env": dict(data.get("env") or {}), "headers": dict(data.get("headers") or {})}


def _save_blob(session: Session, server: McpServer, blob: dict[str, dict[str, str]], user_id: uuid.UUID | None) -> None:
    row = store.find(session, VAULT_PROVIDER, _slot(server))
    if not blob["env"] and not blob["headers"]:
        if row is not None:
            session.delete(row)
        server.secret_hints = {}
        return
    if row is None:
        row = IntegrationSecret(provider=VAULT_PROVIDER, name=_slot(server), ciphertext="")
        session.add(row)
    store.seal(row, json.dumps(blob))
    row.hint = ""
    row.meta = {"server": str(server.id)}
    row.set_by_user_id = user_id
    row.updated_at = row.validated_at = datetime.now(UTC)
    server.secret_hints = {
        "env": {k: mask(v) for k, v in blob["env"].items()},
        "headers": {k: mask(v) for k, v in blob["headers"].items()},
    }


def _clean_secrets(env: dict[str, str] | None, headers: dict[str, str] | None) -> dict[str, dict[str, str]]:
    env, headers = dict(env or {}), dict(headers or {})
    if len(env) + len(headers) > MAX_SECRETS:
        raise Refused(f"At most {MAX_SECRETS} secret values per server.")
    for k, v in env.items():
        if not ENV_NAME.match(k):
            raise Refused(f"{k[:40]!r} is not a valid variable name (letters, digits and _).")
        if not isinstance(v, str) or not v or len(v) > 4000:
            raise Refused(f"Give {k} a value of up to 4,000 characters.")
    for k, v in headers.items():
        if not HEADER_NAME.match(k):
            raise Refused(f"{k[:40]!r} is not a valid header name.")
        if not isinstance(v, str) or not v or len(v) > 4000 or "\n" in v or "\r" in v:
            raise Refused(f"Give {k} a single-line value of up to 4,000 characters.")
    return {"env": env, "headers": headers}


def target_for(session: Session, server: McpServer) -> transport.Target:
    """Plain values for one connection, read on the calling thread (the vault needs the instance in scope)."""
    blob = _blob(session, server)
    return transport.Target(
        transport=server.transport,
        command=server.command,
        args=tuple(str(a) for a in server.args or []),
        url=server.url,
        env=blob["env"],
        headers=blob["headers"],
        timeout_s=server.timeout_s,
        hosted=hosted(),
    )


# --- add, change, remove ------------------------------------------------------------------------------------


def create(
    session: Session,
    *,
    label: str,
    kind: str,
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    user_id: uuid.UUID | None = None,
) -> McpServer:
    label = " ".join((label or "").split())[:80]
    if not label:
        raise Refused("Give the server a name.")
    if (session.scalar(select(func.count(McpServer.id)).where(_mine())) or 0) >= MAX_SERVERS:
        raise Refused(f"You can add up to {MAX_SERVERS} servers.", 409)
    args = [str(a) for a in (args or [])]
    if kind == "stdio":
        if hosted():
            raise Refused("Servers that run a program are not available on this hosted server. Add a remote one.")
        command = (command or "").strip()
        if not command or len(command) > 300 or "\x00" in command:
            raise Refused("Give the command to run (up to 300 characters).")
        if len(args) > MAX_ARGS or any(len(a) > 300 or "\x00" in a for a in args):
            raise Refused(f"At most {MAX_ARGS} arguments of up to 300 characters.")
        if headers:
            raise Refused("A program has environment variables, not headers.")
        url = ""
    elif kind == "http":
        try:
            url = transport.check_url(url, hosted=hosted())
        except transport.McpError as exc:
            raise Refused(str(exc)) from exc
        if env:
            raise Refused("A remote server takes headers, not environment variables.")
        command, args = "", []
    else:
        raise Refused("The server type is stdio or http.")
    blob = _clean_secrets(env, headers)
    slug = slugify(label)
    taken = set(session.scalars(select(McpServer.slug).where(_mine())))
    n, candidate = 2, slug
    while candidate in taken:
        candidate = f"{slug[: 24 - len(str(n)) - 1]}-{n}".strip("-")
        n += 1
    server = McpServer(slug=candidate, label=label, transport=kind, command=command, args=args, url=url)
    session.add(server)
    session.flush()
    _save_blob(session, server, blob, user_id)
    audit(
        session, "mcp", "mcp_server_added", server_id=server.id, slug=server.slug, transport=kind,
        secrets=sorted({*blob["env"], *blob["headers"]}), user_id=user_id,
    )  # fmt: skip
    return server


def update(
    session: Session,
    server: McpServer,
    *,
    enabled: bool | None = None,
    timeout_s: int | None = None,
    calls_per_hour: int | None = None,
    user_id: uuid.UUID | None = None,
) -> McpServer:
    changed: dict[str, Any] = {}
    if enabled is not None and enabled != server.enabled:
        server.enabled = enabled
        changed["enabled"] = enabled
    if timeout_s is not None:
        if not 1 <= timeout_s <= 120:
            raise Refused("The time limit is 1 to 120 seconds.")
        server.timeout_s = changed["timeout_s"] = timeout_s
    if calls_per_hour is not None:
        if not 1 <= calls_per_hour <= 1000:
            raise Refused("The limit is 1 to 1,000 calls an hour.")
        server.calls_per_hour = changed["calls_per_hour"] = calls_per_hour
    if changed:
        audit(session, "mcp", "mcp_server_changed", server_id=server.id, slug=server.slug, user_id=user_id, **changed)
    return server


def remove(session: Session, server: McpServer, *, user_id: uuid.UUID | None = None) -> None:
    for pinned in session.scalars(select(McpPin).where(McpPin.skill_id == PIN_SKILL, McpPin.server == str(server.id))):
        session.delete(pinned)
    secret = store.find(session, VAULT_PROVIDER, _slot(server))
    if secret is not None:
        session.delete(secret)
    audit(session, "mcp", "mcp_server_removed", server_id=server.id, slug=server.slug, user_id=user_id)
    session.delete(server)


# --- tools, approval and risk --------------------------------------------------------------------------------


def refresh(session: Session, server: McpServer, *, user_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Connect, list the tools, keep the listing. Raises transport.McpError (also recorded on the server)."""
    target = target_for(session, server)
    try:
        tools = transport.list_tools(target)
    except transport.McpError as exc:
        server.last_error = str(exc)[:300]
        audit(session, "mcp", "mcp_server_test_failed", server_id=server.id, slug=server.slug, user_id=user_id)
        raise
    server.tools = [t for t in tools if t["name"] and len(json.dumps(t["inputSchema"])) <= MAX_SCHEMA_CHARS]
    server.listed_at = datetime.now(UTC)
    server.last_error = ""
    audit(
        session, "mcp", "mcp_server_tested", server_id=server.id, slug=server.slug, tools=len(server.tools),
        skipped=len(tools) - len(server.tools), user_id=user_id,
    )  # fmt: skip
    return server.tools


def _pins(session: Session, server: McpServer) -> dict[str, str]:
    rows = session.scalars(select(McpPin).where(McpPin.skill_id == PIN_SKILL, McpPin.server == str(server.id)))
    return {r.tool: r.hash for r in rows}


def approval_state(pins: dict[str, str], tool: dict[str, Any]) -> str:
    """approved | changed (approved before, but the server describes it differently now) | new"""
    have = pins.get(tool["name"])
    if have is None:
        return "new"
    return "approved" if have == tool_hash(tool) else "changed"


def approve(
    session: Session, server: McpServer, names: list[str] | None = None, *, user_id: uuid.UUID | None = None
) -> int:
    """The owner read these tools as listed and approved them (all of the last listing when `names` is None)."""
    listed = {t["name"]: t for t in server.tools or []}
    wanted = list(listed) if names is None else names
    missing = [n for n in wanted if n not in listed]
    if missing:
        raise Refused(f"Not in the server's last listing: {', '.join(m[:40] for m in missing[:3])}. Test it again.")
    return pin_tools(
        session, skill_id=PIN_SKILL, server=str(server.id), tools=[listed[n] for n in wanted],
        by=str(user_id or "owner"),
    )  # fmt: skip


def set_risk(session: Session, server: McpServer, tool: str, risk: str, *, user_id: uuid.UUID | None = None) -> None:
    listed = {t["name"]: t for t in server.tools or []}
    if tool not in listed:
        raise Refused("That tool is not in the server's last listing. Test the server again.", 404)
    if risk not in RISKS:
        raise Refused(f"The risk class is one of: {', '.join(RISKS)}.")
    server.risk = {**(server.risk or {}), tool: {"class": risk, "hash": tool_hash(listed[tool])}}
    audit(session, "mcp", "mcp_risk_set", server_id=server.id, tool=tool, risk=risk, user_id=user_id)


def effective_risk(server: McpServer, tool: dict[str, Any]) -> str:
    """The owner's class if it was set for this exact tool definition; otherwise write, or destructive when the
    server itself says destructive (a hint can only make a tool stricter, never looser)."""
    mine = (server.risk or {}).get(tool["name"])
    if isinstance(mine, dict) and mine.get("class") in RISKS and mine.get("hash") == tool_hash(tool):
        return str(mine["class"])
    if (tool.get("annotations") or {}).get("destructiveHint") is True:
        return "destructive"
    return DEFAULT_RISK


def view(session: Session, server: McpServer) -> dict[str, Any]:
    """What Settings shows. Never a secret: names and masked hints only."""
    pins = _pins(session, server)
    tools = []
    for t in server.tools or []:
        hints = t.get("annotations") or {}
        tools.append(
            {
                "name": t["name"],
                "description": t["description"],
                "risk": effective_risk(server, t),
                "risk_set": t["name"] in (server.risk or {})
                and effective_risk(server, t) == server.risk[t["name"]]["class"],
                "approval": approval_state(pins, t),
                "server_says": {k: hints[k] for k in ("readOnlyHint", "destructiveHint") if k in hints},
                "input_schema": t["inputSchema"],
            }
        )
    return {
        "id": str(server.id),
        "slug": server.slug,
        "label": server.label,
        "transport": server.transport,
        "command": server.command,
        "args": list(server.args or []),
        "url": server.url,
        "enabled": server.enabled,
        "timeout_s": server.timeout_s,
        "calls_per_hour": server.calls_per_hour,
        "secrets": server.secret_hints or {},
        "listed_at": server.listed_at.isoformat() if server.listed_at else None,
        "last_error": server.last_error,
        "tools": tools,
        "removed_tools": sorted(set(pins) - {t["name"] for t in server.tools or []}),
    }


def calls_in_the_last_hour(session: Session, server: McpServer) -> int:
    since = datetime.now(UTC) - timedelta(hours=1)
    return int(
        session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.agent_id == scope_or_default().agent_id,
                AuditLog.action == "mcp_tool_called",
                AuditLog.created_at >= since,
                AuditLog.detail["server_id"].astext == str(server.id),
            )
        )
        or 0
    )
