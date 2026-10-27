"""Pin an MCP server's tool manifest by hash (issue #116).

When the owner approves a skill's MCP tools, each tool is hashed: (name, description, inputSchema,
annotations), canonical JSON. Whenever the server's `tools/list` result is read again (at the latest when its
`ttlMs` runs out, spec 2026-07-28) every tool is compared with its pin. A tool that changed, and a tool that is
new, is blocked and shown to the owner as a diff until they approve it again. Unchanged tools keep working.
The check is code in the client; the model never sees a blocked tool and cannot argue with it.

Spec 2026-07-28 notes this module relies on: there is no `initialize` handshake or session to depend on,
`tools/list` results carry `ttlMs` and `cacheScope`, and `tools/list` should return tools in a deterministic
order (we sort by name anyway, so a server that doesn't still hashes the same).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.mcp.models import McpPin
from superaide.platform.audit import audit

DEFAULT_TTL_MS = 60_000  # when a server does not say (older servers): re-check every minute
MAX_TTL_MS = 3_600_000  # whatever a server asks, never trust a list for longer than an hour


def canonical(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(tool.get("name", "")),
        "description": str(tool.get("description", "")),
        "inputSchema": tool.get("inputSchema") or {},
        "annotations": tool.get("annotations") or {},
    }


def tool_hash(tool: dict[str, Any]) -> str:
    text = json.dumps(canonical(tool), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def ttl_of(result: dict[str, Any]) -> int:
    """How long this `tools/list` result may be used, in ms, bounded. A missing or bad value is the short default."""
    raw = result.get("ttlMs")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return DEFAULT_TTL_MS
    return min(raw, MAX_TTL_MS)


@dataclass
class Change:
    tool: str
    kind: str  # changed | new | removed
    fields: list[str] = field(default_factory=list)  # which parts changed: description, inputSchema, annotations


@dataclass
class Check:
    allowed: list[str] = field(default_factory=list)
    blocked: list[Change] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    recheck_at: datetime | None = None

    @property
    def clean(self) -> bool:
        return not self.blocked


def pin(session: Session, *, skill_id: str, server: str, tools: list[dict[str, Any]], by: str) -> int:
    """The owner approved these tools: record them (replacing earlier pins for this skill and server)."""
    now = datetime.now(UTC)
    rows = {
        r.tool: r for r in session.scalars(select(McpPin).where(McpPin.skill_id == skill_id, McpPin.server == server))
    }
    names = set()
    for t in tools:
        c = canonical(t)
        names.add(c["name"])
        row = rows.get(c["name"])
        if row is None:
            row = McpPin(skill_id=skill_id, server=server, tool=c["name"])
            session.add(row)
        row.hash, row.snapshot, row.approved_at = tool_hash(t), c, now
    for gone, row in rows.items():
        if gone not in names:
            session.delete(row)
    audit(session, "mcp", "mcp_pinned", skill_id=skill_id, server=server, tools=sorted(names), by=by)
    return len(names)


def check(
    session: Session, *, skill_id: str, server: str, result: dict[str, Any], now: datetime | None = None
) -> Check:
    """Compare a fresh `tools/list` result with the pins."""
    now = now or datetime.now(UTC)
    pins = {
        r.tool: r for r in session.scalars(select(McpPin).where(McpPin.skill_id == skill_id, McpPin.server == server))
    }
    out = Check(recheck_at=now + timedelta(milliseconds=ttl_of(result)))
    seen = set()
    for t in sorted(result.get("tools") or [], key=lambda t: str(t.get("name", ""))):
        c = canonical(t)
        seen.add(c["name"])
        row = pins.get(c["name"])
        if row is None:
            out.blocked.append(Change(c["name"], "new"))
        elif row.hash == tool_hash(t):
            out.allowed.append(c["name"])
        else:
            old = row.snapshot or {}
            out.blocked.append(
                Change(
                    c["name"],
                    "changed",
                    [k for k in ("description", "inputSchema", "annotations") if old.get(k) != c[k]],
                )
            )
    out.removed = sorted(set(pins) - seen)
    if out.blocked:
        audit(
            session, "mcp", "mcp_tool_blocked", skill_id=skill_id, server=server,
            tools=[f"{c.tool}:{c.kind}" for c in out.blocked],
        )  # fmt: skip
    return out


def describe(session: Session, *, skill_id: str, server: str, change: Change, fresh: dict[str, Any]) -> list[str]:
    """Lines for the owner: what the server now says next to what they approved."""
    row = session.scalar(
        select(McpPin).where(McpPin.skill_id == skill_id, McpPin.server == server, McpPin.tool == change.tool)
    )
    old = (row.snapshot if row else None) or {}
    new = canonical(fresh)
    if change.kind == "new":
        return [f"New tool {change.tool}: {new['description']}"]
    lines = []
    for f in change.fields:
        lines.append(f"{change.tool} {f} was: {json.dumps(old.get(f), ensure_ascii=False)}")
        lines.append(f"{change.tool} {f} is now: {json.dumps(new[f], ensure_ascii=False)}")
    return lines


def may_call(
    session: Session, *, skill_id: str, server: str, tool: dict[str, Any], enabled: bool | None = None
) -> bool:
    """The client's gate just before a call: the tool as the server describes it right now must match its pin."""
    from superaide.platform.config import get_settings

    if not (get_settings().mcp_pinning if enabled is None else enabled):
        return True
    row = session.scalar(
        select(McpPin).where(McpPin.skill_id == skill_id, McpPin.server == server, McpPin.tool == str(tool.get("name")))
    )
    return row is not None and row.hash == tool_hash(tool)
