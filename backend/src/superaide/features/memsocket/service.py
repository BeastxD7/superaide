"""The Memory Socket (#138): read-only, audience-gated memory for the owner's other assistants.

Scopes, chosen by the owner per consumer (a third-party model receives what is returned, so the owner opts in):
- `public`: only memories marked public (the audience of a stranger).
- `owner`: what the owner may see at the link-level of trust; memories that need a step-up never leave.
The gate is `context.audience.memory_filter` inside the SQL query, so nothing outside the scope is ever loaded
into the response. One tool, citation text only, no write tools. Off unless MEMORY_SOCKET_ENABLED.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.audit import audit
from superaide.config import get_settings
from superaide.context.audience import Audience
from superaide.context.recall import recall_memories
from superaide.features.memsocket.models import MemorySocketClient
from superaide.identity.trust import Trust
from superaide.tenancy.context import InstanceScope, scope_or_default

SCOPES = ("public", "owner")
TOKEN_PREFIX = "sa_mem_"
MAX_QUESTION, MAX_RESULTS, MAX_TEXT = 300, 6, 600
_calls: dict[uuid.UUID, deque[float]] = defaultdict(deque)


def enabled() -> bool:
    return get_settings().memory_socket_enabled


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_client(session: Session, *, name: str, scope: str) -> tuple[MemorySocketClient, str]:
    """A new consumer and its token, shown once: only the hash is kept."""
    if scope not in SCOPES:
        raise ValueError("scope must be public or owner")
    here = scope_or_default()
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = MemorySocketClient(
        tenant_id=here.tenant_id, agent_id=here.agent_id, name=name[:60], scope=scope, token_hash=hash_token(token)
    )
    session.add(row)
    session.flush()
    audit(session, "owner", "memory_socket_client_added", client=row.name, scope=scope)
    return row, token


def clients(session: Session) -> list[MemorySocketClient]:
    return list(
        session.scalars(
            select(MemorySocketClient)
            .where(MemorySocketClient.agent_id == scope_or_default().agent_id)
            .order_by(MemorySocketClient.created_at)
        ).all()
    )


def revoke_client(session: Session, client_id: uuid.UUID) -> bool:
    row = session.get(MemorySocketClient, client_id)
    if row is None or row.agent_id != scope_or_default().agent_id or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(UTC)
    audit(session, "owner", "memory_socket_client_revoked", client=row.name)
    return True


def find(session: Session, token: str) -> MemorySocketClient | None:
    """The live consumer a token belongs to (looked up by hash, across instances on purpose)."""
    if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    row = session.scalar(select(MemorySocketClient).where(MemorySocketClient.token_hash == hash_token(token)))
    return row if row is not None and row.revoked_at is None else None


def scope_of(row: MemorySocketClient) -> InstanceScope:
    return InstanceScope(tenant_id=row.tenant_id, agent_id=row.agent_id)


def within_limit(client_id: uuid.UUID, *, now: float | None = None) -> bool:
    """Sliding one-minute window per consumer, in this process."""
    t = time.monotonic() if now is None else now
    q = _calls[client_id]
    while q and t - q[0] >= 60:
        q.popleft()
    if len(q) >= get_settings().memory_socket_per_minute:
        return False
    q.append(t)
    return True


def audience_for_scope(session: Session, scope: str) -> Audience:
    from superaide.app.accounts import owner

    if scope == "owner":
        me = owner(session)
        if me is not None:
            return Audience(person_ids={me.id}, has_unknown=False, min_trust=int(Trust.LINKED), all_internal=True)
    return Audience(person_ids=set(), has_unknown=True, min_trust=0, all_internal=False)  # public memories only


def ask(session: Session, client: MemorySocketClient, question: str) -> str:
    """Citation text for the question, from memories inside the consumer's scope, and the access-log entry."""
    question = " ".join(question.split())[:MAX_QUESTION]
    found = recall_memories(session, audience_for_scope(session, client.scope), question, limit=MAX_RESULTS)
    client.last_used_at = datetime.now(UTC)
    audit(session, "memory_socket", "query", client=client.name, scope=client.scope, returned=len(found))
    if not found:
        return "No memory found for that question."
    return "\n\n".join(f"[{i}] {s.text[:MAX_TEXT]} ({s.title}; {s.uri})" for i, s in enumerate(found, 1))


def view(row: MemorySocketClient) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "scope": row.scope,
        "created_at": row.created_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked": row.revoked_at is not None,
    }
