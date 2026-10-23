"""Admin API for the operator console. Bearer token required.

Read-only except for the kill switch and linking or unlinking an identity.

Metadata by default, content only by break-glass (ADR 0009 T8). The overview, spend,
the list of hired instances and the kill switch show no owner's content. Anything that
shows or changes what an owner's Ava holds (people, conversations, messages, memories,
approvals, the safety log, linking) needs a reason (`X-Operator-Reason`, at least
10 characters) and names the instance (`X-Operator-Instance`, else the server's first).
A header can only carry plain Latin-1, so the console sends the reason percent-encoded
with `X-Operator-Reason-Encoding: uri`; plain text without that header works too.
Each such request is written to that owner's own audit log, which the owner can read
(/app/access-log), and runs inside that instance only.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import func, select, text

from superaide.app.accounts import owner as hired_owner
from superaide.audit import audit
from superaide.brain.budget import BudgetGuard
from superaide.config import get_settings
from superaide.db.models import (
    Approval,
    AuditLog,
    ChannelIdentity,
    Context,
    Event,
    IdentityLinkEvent,
    MemoryItem,
    Onboarding,
    Person,
    SpendEntry,
)
from superaide.db.session import session_scope
from superaide.factory import brain_ready
from superaide.identity.linking import operator_link, unlink
from superaide.pagination import (
    Page,
    SortKey,
    bind_for,
    cursor_param,
    limit_param,
    paginate,
    set_next_headers,
)
from superaide.policy import binding
from superaide.policy.killswitch import instance_frozen, is_frozen, set_platform_frozen
from superaide.tenancy.context import InstanceScope, default_scope, use_instance
from superaide.tenancy.models import AgentInstance, ChannelEndpoint, Tenant

admin_bearer = HTTPBearer(scheme_name="Admin token", description="`ADMIN_TOKEN` from `.env`.", auto_error=False)


def require_admin(credentials: HTTPAuthorizationCredentials | None = Security(admin_bearer)) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(503, "Set ADMIN_TOKEN in .env to use the admin API")
    supplied = credentials.credentials.strip() if credentials else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "Bad admin token")


router = APIRouter(prefix="/api", tags=["Operator console"], dependencies=[Depends(require_admin)])

MIN_REASON = 10
MESSAGE_COUNT_CAP = 1000  # a conversation's message count is read up to here (no COUNT(*) over a long one)


def _reason(request: Request) -> str:
    """The operator's reason as typed. Headers cannot hold curly quotes, dashes or other scripts,
    so the console percent-encodes it and says so (`X-Operator-Reason-Encoding: uri`)."""
    raw = request.headers.get("x-operator-reason") or ""
    if request.headers.get("x-operator-reason-encoding", "").strip().lower() == "uri":
        raw = unquote(raw)
    return " ".join(raw.split())


ACCESS_LOG_WINDOW = timedelta(minutes=10)


def _recently_logged(session: Any, path: str, reason: str) -> bool:
    """The same screen was already opened with the same reason a moment ago: one visit is one entry in the owner's
    log, not one per poll (the console refreshes every few seconds; issue #286). Changes (POST) are always logged."""
    since = datetime.now(UTC) - ACCESS_LOG_WINDOW
    rows = session.scalars(
        select(AuditLog).where(
            AuditLog.actor == "operator", AuditLog.action == "operator_access", AuditLog.created_at > since
        )
    )
    return any((r.detail or {}).get("path") == path and (r.detail or {}).get("reason") == reason for r in rows)


def _grant(request: Request) -> InstanceScope:
    reason = _reason(request)
    if len(reason) < MIN_REASON:
        raise HTTPException(
            403,
            {
                "code": "reason_required",
                "message": "Seeing an owner's content needs a reason (at least 10 characters). It is recorded "
                "in the owner's own log.",
            },
        )
    wanted = request.headers.get("x-operator-instance") or ""
    with session_scope() as s:
        if wanted:
            try:
                instance = s.get(AgentInstance, uuid.UUID(wanted))
            except ValueError:
                instance = None
            if instance is None:
                raise HTTPException(404, "No such instance")
            scope = InstanceScope(tenant_id=instance.tenant_id, agent_id=instance.id)
        else:
            scope = default_scope()
    with use_instance(scope), session_scope() as s:
        if request.method != "GET" or not _recently_logged(s, request.url.path, reason[:500]):
            audit(
                s, "operator", "operator_access", path=request.url.path, method=request.method,
                reason=reason[:500], query=str(request.url.query)[:300],
            )  # fmt: skip
    return scope


async def break_glass(request: Request) -> AsyncIterator[InstanceScope]:
    """Owner content: a reason, recorded in that owner's log, and only that instance in scope."""
    scope = await run_in_threadpool(_grant, request)
    with use_instance(scope):
        yield scope


content = [Depends(break_glass)]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# "account_signed_in": the owner's own web identity, made by signing in to the account (app/accounts.py).
VERIFIED_LINK_METHODS = ("account_signed_in", "code_via_trusted_channel", "operator_bootstrap", "operator_dashboard")


def _live_memories() -> Any:
    """What the owner's notebook lists: not forgotten, not waiting for a decision (same rule as GET /app/notebook)."""
    return (
        select(func.count())
        .select_from(MemoryItem)
        .where(MemoryItem.valid_to.is_(None), MemoryItem.conflict_status == "none")
    )


def _runtime_live() -> dict[str, bool]:
    """What is connected on this server, as the app sees it: the server's .env plus the credentials each hired Ava
    saved in the app (the vault), not .env alone (issues #277, #278). Metadata only."""
    from superaide.tenancy.service import active_scopes
    from superaide.vault.store import runtime_settings

    env = get_settings()
    live = {
        "brain": brain_ready(env),
        "telegram": bool(env.telegram_bot_token),
        "slack": bool(env.slack_bot_token and env.slack_app_token),
    }
    with session_scope() as s:
        scopes = active_scopes(s)
    for scope in scopes:
        try:
            with use_instance(scope):
                mine = runtime_settings()
                live["brain"] = live["brain"] or brain_ready(mine)
                live["telegram"] = live["telegram"] or bool(mine.telegram_bot_token)
                live["slack"] = live["slack"] or bool(mine.slack_bot_token and mine.slack_app_token)
        except Exception:  # one instance's trouble never hides the others
            continue
    return live


def _audit_count(s: Any, *actions: str) -> int:
    return int(s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(actions))) or 0)


@router.get(
    "/instances",
    summary="Hired instances",
    description=(
        "Every hired Ava on this server, metadata only: when, its state, how much it holds and has spent. "
        "No owner's name, email or content."
    ),
)
def instances() -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(
            select(AgentInstance, Tenant.region).join(Tenant, Tenant.id == AgentInstance.tenant_id, isouter=True)
            .order_by(AgentInstance.created_at)
        ).all()  # fmt: skip
        listed = [(InstanceScope(tenant_id=i.tenant_id, agent_id=i.id), i, region) for i, region in rows]
    day_ago = datetime.now(UTC) - timedelta(days=1)
    out = []
    for scope, instance, region in listed:
        with use_instance(scope), session_scope() as s:  # counted inside the instance (RLS allows only that)
            out.append(
                {
                    "id": str(instance.id),
                    "agent": instance.agent_slug,
                    "status": instance.status,
                    "since": _iso(instance.created_at),
                    "region": region or "",
                    "key_source": instance.key_source,
                    "frozen": instance_frozen(s, scope),
                    "channels": sorted(
                        {
                            e.provider
                            for e in s.scalars(
                                select(ChannelEndpoint).where(ChannelEndpoint.agent_id == scope.agent_id)
                            )
                        }
                    ),
                    "counts": {
                        "people": s.scalar(select(func.count()).select_from(Person)) or 0,
                        "conversations": s.scalar(select(func.count()).select_from(Context)) or 0,
                        "messages_24h": s.scalar(
                            select(func.count()).select_from(Event).where(Event.created_at > day_ago)
                        )
                        or 0,
                        "memories": s.scalar(_live_memories()) or 0,
                    },
                    "spend_usd": float(
                        s.scalar(
                            select(func.coalesce(func.sum(SpendEntry.cost_usd), 0)).where(
                                SpendEntry.status == "settled"
                            )
                        )
                        or 0
                    ),
                }
            )
    return out


@router.get(
    "/overview",
    summary="Overview",
    description="Growth stage, what is live (brain, channels), spend against the cap and the kill switch.",
)
def overview() -> dict[str, Any]:
    settings = get_settings()
    spend = BudgetGuard.for_instance(settings).summary().as_dict()
    day_ago = datetime.now(UTC) - timedelta(days=1)
    live = _runtime_live()
    with session_scope() as s:
        introduced = int(
            s.scalar(select(func.count()).select_from(Onboarding).where(Onboarding.completed_at.is_not(None))) or 0
        )
        unverified_links = int(
            s.scalar(
                select(func.count())
                .select_from(IdentityLinkEvent)
                .where(IdentityLinkEvent.action == "link", IdentityLinkEvent.method.not_in(VERIFIED_LINK_METHODS))
            )
            or 0
        )
        stranger_messages = int(
            s.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.direction == "in", Event.resolved_person_id.is_(None))
            )
            or 0
        )
        return {
            "agent_name": settings.agent_name,
            "stage": "Infant",
            "frozen": is_frozen(s),
            "model_mode": "Claude API (real replies)" if live["brain"] else "offline (no API key, replies are echoes)",
            "channels": {"telegram": live["telegram"], "slack": live["slack"]},
            "simulator": settings.enable_simulator,
            "spend": spend,
            # Gates from Infant to Child. Most are measured from phase 2 onward.
            "next_stage": "Child",
            "gates": [
                {
                    "label": "Onboarding interview done",
                    "status": "met" if introduced else "later",
                    "detail": f"{introduced} owner(s) finished the introduction" if introduced else "Nobody has yet",
                },
                {"label": "Past posts ingested", "status": "later", "detail": "Arrives in phase 2"},
                {
                    "label": "20 corrections absorbed",
                    "status": "later",
                    "detail": "0 of 20; corrections arrive in phase 2",
                },
                {
                    "label": "No identity failures",
                    "status": "met" if unverified_links == 0 else "unmet",
                    "detail": f"{unverified_links} links made without a verified step",
                },
            ],
            "safety": {
                "stranger_messages": stranger_messages,
                "link_codes_sent": _audit_count(s, "link_challenge_sent"),
                "links_made": _audit_count(s, "linked"),
                "refused_actions": _audit_count(s, "action_refused", "link_refused"),
                "rejected_webhooks": _audit_count(s, "rejected", "dropped_unverified"),
                "refused_model_calls": _audit_count(s, "llm_call_refused"),
            },
            "counts": {
                "people": s.scalar(select(func.count()).select_from(Person)),
                "identities": s.scalar(select(func.count()).select_from(ChannelIdentity)),
                "linked_identities": s.scalar(
                    select(func.count()).select_from(ChannelIdentity).where(ChannelIdentity.person_id.is_not(None))
                ),
                "conversations": s.scalar(select(func.count()).select_from(Context)),
                "messages_24h": s.scalar(select(func.count()).select_from(Event).where(Event.created_at > day_ago)),
                "memories": s.scalar(_live_memories()),
                "pending_approvals": s.scalar(select(func.count()).select_from(Approval).where(binding.waiting())),
                "refused_actions": s.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action.in_(["action_refused", "llm_call_refused"]))
                ),
            },
        }


@router.get(
    "/people",
    dependencies=content,
    summary="People and trust",
    description="Everyone Ava knows, their relation to the owner and each linked channel identity with its trust tier.",
)
def people() -> list[dict[str, Any]]:
    with session_scope() as s:
        persons = {p.id: p for p in s.scalars(select(Person).order_by(Person.created_at)).all()}
        identities = s.scalars(select(ChannelIdentity).order_by(ChannelIdentity.created_at)).all()
        links = s.scalars(select(IdentityLinkEvent).order_by(IdentityLinkEvent.created_at.desc())).all()
        out: dict[str, dict[str, Any]] = {}
        for p in persons.values():
            out[str(p.id)] = {
                "id": str(p.id),
                "name": p.display_name,
                "relation": p.relation,
                "status": p.status,
                "identities": [],
                "link_events": [],
            }
        strangers: list[dict[str, Any]] = []
        for i in identities:
            row = {
                "id": str(i.id),
                "provider": i.provider,
                "tenant_scope": i.tenant_scope,
                "stable_key": i.stable_key,
                "display_name": (i.aux_handles or {}).get("display_name")
                or (i.aux_handles or {}).get("username")
                or "",
                "trust_level": i.trust_level,
                "method": (i.verification or {}).get("method", ""),
                "risk_flags": i.risk_flags or [],
                "last_seen_at": _iso(i.last_seen_at),
            }
            if i.person_id and str(i.person_id) in out:
                out[str(i.person_id)]["identities"].append(row)
            else:
                strangers.append(row)
        for e in links:
            if e.person_id and str(e.person_id) in out:
                out[str(e.person_id)]["link_events"].append(
                    {"action": e.action, "method": e.method, "actor": e.actor, "at": _iso(e.created_at)}
                )
        result = list(out.values())
        if strangers:
            result.append(
                {
                    "id": "unlinked",
                    "name": "Unlinked accounts",
                    "relation": "unknown",
                    "status": "stranger",
                    "identities": strangers,
                    "link_events": [],
                }
            )
        return result


@router.get(
    "/conversations",
    dependencies=content,
    summary="Conversations",
    description="Every chat Ava is in, with its audience.",
)
def conversations() -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(
            select(Context, func.count(Event.id), func.max(Event.created_at))
            .join(Event, Event.context_id == Context.id, isouter=True)
            .group_by(Context.id)
            .order_by(func.max(Event.created_at).desc().nulls_last())
        ).all()
        return [
            {
                "id": str(c.id),
                "provider": c.provider,
                "kind": c.kind,
                "audience_class": c.audience_class,
                "title": c.title or c.external_id,
                "messages": n,
                "last_at": _iso(last),
            }
            for c, n, last in rows
        ]


@router.get(
    "/conversations/page",
    dependencies=content,
    summary="Conversations, newest activity first (paged)",
    description="The same rows as `/api/conversations`, ordered by when each last moved (`last_event_at`) and paged "
    "by cursor. `messages` counts up to 1000 (`messages_more` is true beyond that): an exact count over a long "
    "conversation is the scan this route exists to avoid. ADR 0014.",
    response_model=Page[dict[str, Any]],
)
def conversations_page(limit: int = limit_param(50, 100), cursor: str | None = cursor_param()) -> Page[dict[str, Any]]:
    with session_scope() as s:
        page = paginate(
            s,
            select(Context),
            [SortKey(Context.last_event_at), SortKey(Context.id)],
            endpoint="op-conversations",
            bind=bind_for("operator"),
            limit=limit,
            cursor=cursor,
            maximum=100,
            build=lambda c: c,
        )
        counts: dict[uuid.UUID, int] = {}
        if page.items:
            rows = s.execute(
                text(
                    "SELECT c.id, (SELECT count(*) FROM (SELECT 1 FROM event e WHERE e.context_id = c.id "
                    f"LIMIT {MESSAGE_COUNT_CAP + 1}) x) FROM unnest(CAST(:ids AS uuid[])) AS c(id)"
                ),
                {"ids": [c.id for c in page.items]},
            ).all()
            counts = {r[0]: int(r[1]) for r in rows}
        items = [
            {
                "id": str(c.id),
                "provider": c.provider,
                "kind": c.kind,
                "audience_class": c.audience_class,
                "title": c.title or c.external_id,
                "messages": min(counts.get(c.id, 0), MESSAGE_COUNT_CAP),
                "messages_more": counts.get(c.id, 0) > MESSAGE_COUNT_CAP,
                "last_at": _iso(c.last_event_at),
            }
            for c in page.items
        ]
    return Page[dict[str, Any]](items=items, next_cursor=page.next_cursor, has_more=page.has_more, total_estimate=None)


@router.get(
    "/events",
    dependencies=content,
    summary="Messages",
    description="Recent messages, newest first in pages, optionally for one conversation (`context_id`). `limit` is "
    "1 to 200, clamped (default 100). When older ones exist the response has `X-Next-Cursor`, `Link: rel=next` and "
    "`X-Has-More: true`; send the cursor back as `cursor`. The body stays a bare array. ADR 0014.",
)
def events(
    request: Request,
    response: Response,
    context_id: uuid.UUID | None = None,
    limit: int = 100,
    cursor: str | None = cursor_param(),
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))  # clamped, as before (the console's contract), now at 200 instead of 500
    q: Any = select(Event, Person.display_name).join(Person, Person.id == Event.resolved_person_id, isouter=True)
    if context_id:
        q = q.where(Event.context_id == context_id)
    with session_scope() as s:
        page = paginate(
            s,
            q,
            [SortKey(Event.created_at), SortKey(Event.id)],
            endpoint=f"op-events:{context_id or 'all'}",
            bind=bind_for("operator"),
            limit=limit,
            cursor=cursor,
            maximum=200,
            entity_of=lambda row: row[0],
            build=lambda row: {
                "id": str(row[0].id),
                "context_id": str(row[0].context_id),
                "direction": row[0].direction,
                "sender": row[1] or ("agent" if row[0].direction == "out" else "stranger"),
                "trust": row[0].trust_at_time,
                "audience_size": len(row[0].audience_person_ids or []),
                "audience_has_unknown": row[0].audience_has_unknown,
                "content": row[0].content,
                "at": _iso(row[0].created_at),
            },
        )
    response.headers["X-Has-More"] = "true" if page.has_more else "false"
    set_next_headers(response, str(request.url), page.next_cursor)
    return page.items


@router.get(
    "/memories", dependencies=content, summary="Memories", description="Stored memories and who may see each one."
)
def memories() -> list[dict[str, Any]]:
    with session_scope() as s:
        names = {p.id: p.display_name for p in s.scalars(select(Person)).all()}
        return [
            {
                "id": str(m.id),
                "content": m.content,
                "allowed_kind": m.allowed_kind,
                "allowed_people": [names.get(i, str(i)) for i in (m.allowed_person_ids or [])],
                "min_trust": m.min_trust,
                "at": _iso(m.created_at),
            }
            for m in s.scalars(select(MemoryItem).order_by(MemoryItem.created_at.desc())).all()
        ]


@router.get(
    "/approvals", dependencies=content, summary="Pending approvals", description="Actions waiting for the owner's yes."
)
def approvals() -> list[dict[str, Any]]:
    with session_scope() as s:
        names = {p.id: p.display_name for p in s.scalars(select(Person)).all()}
        return [
            {
                "id": str(a.id),
                "kind": a.kind,
                "summary": (a.payload or {}).get("summary", ""),
                "requested_by": names.get(a.requested_by_person_id, "unknown")
                if a.requested_by_person_id
                else "unknown",
                "status": a.status,
                "at": _iso(a.created_at),
            }
            for a in s.scalars(select(Approval).order_by(Approval.created_at.desc())).all()
        ]


@router.get(
    "/audit",
    dependencies=content,
    summary="Safety log",
    description="The audit trail: identity checks, refusals, links, spend and sign-ins.",
)
def audit_log(limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 1000))
    with session_scope() as s:
        return [
            {"id": str(a.id), "actor": a.actor, "action": a.action, "detail": a.detail, "at": _iso(a.created_at)}
            for a in s.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).all()
        ]


@router.get("/spend", summary="Spend", description="Claude API spend against `MAX_SPEND_USD`, by purpose and model.")
def spend() -> dict[str, Any]:
    settings = get_settings()
    summary = BudgetGuard.for_instance(settings).summary().as_dict()
    with session_scope() as s:
        entries = s.scalars(select(SpendEntry).order_by(SpendEntry.created_at.desc()).limit(200)).all()
        return {
            "summary": summary,
            "entries": [
                {
                    "id": str(e.id),
                    "purpose": e.purpose,
                    "model": e.model,
                    "status": e.status,
                    "cost_usd": float(e.cost_usd),
                    "reserved_usd": float(e.reserved_usd),
                    "input_tokens": e.input_tokens,
                    "output_tokens": e.output_tokens,
                    "cache_read_tokens": e.cache_read_tokens,
                    "at": _iso(e.created_at),
                }
                for e in entries
            ],
        }


class KillBody(BaseModel):
    on: bool


@router.post(
    "/kill",
    summary="Kill switch",
    description='`{"on": true}` stops all model calls and replies of every hired Ava on this server; `false` resumes.',
)
def kill(body: KillBody) -> dict[str, bool]:
    with session_scope() as s:
        set_platform_frozen(s, body.on, actor="dashboard")
    return {"frozen": body.on}


@router.post(
    "/identities/{identity_id}/unlink",
    dependencies=content,
    summary="Unlink an identity",
    description="Detaches a channel account from its person; it is treated as a stranger again.",
)
def unlink_identity(identity_id: uuid.UUID) -> dict[str, str]:
    with session_scope() as s:
        identity = s.get(ChannelIdentity, identity_id)
        if identity is None:
            raise HTTPException(404, "No such identity")
        if identity.person_id is None:
            raise HTTPException(409, "Already unlinked")
        boss = hired_owner(s)
        if identity.provider == "web" and boss is not None and identity.person_id == boss.id:
            # The owner's own sign-in identity: without it chat treats the owner as a stranger while the rest of the
            # app (which goes by the session) looks normal (issue #285).
            raise HTTPException(
                409, "This is the owner's own sign-in on the app. Unlinking it would lock them out of chat."
            )
        unlink(s, identity, actor="dashboard", reason="unlinked from dashboard")
    return {"status": "unlinked"}


class LinkBody(BaseModel):
    person_id: uuid.UUID


@router.post(
    "/identities/{identity_id}/link",
    dependencies=content,
    summary="Link an identity",
    description="Links a channel account to a person by hand (operator override).",
)
def link_identity(identity_id: uuid.UUID, body: LinkBody) -> dict[str, str]:
    """Operator action from your own dashboard: attach an unlinked account to a person."""
    with session_scope() as s:
        identity = s.get(ChannelIdentity, identity_id)
        person = s.get(Person, body.person_id)
        if identity is None or person is None:
            raise HTTPException(404, "No such identity or person")
        if identity.person_id is not None:
            raise HTTPException(409, "Already linked; unlink it first")
        try:
            operator_link(s, identity, person, actor="dashboard")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"status": "linked"}
