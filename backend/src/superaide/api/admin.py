"""Admin API for the dashboard. Localhost only, bearer token required.

Read-only except for two writes: the kill switch and unlinking an identity.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import func, select

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
    Person,
    SpendEntry,
)
from superaide.db.session import session_scope
from superaide.factory import brain_ready
from superaide.identity.linking import operator_link, unlink
from superaide.policy.killswitch import is_frozen, set_frozen

admin_bearer = HTTPBearer(scheme_name="Admin token", description="`ADMIN_TOKEN` from `.env`.", auto_error=False)


def require_admin(credentials: HTTPAuthorizationCredentials | None = Security(admin_bearer)) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(503, "Set ADMIN_TOKEN in .env to use the admin API")
    supplied = credentials.credentials.strip() if credentials else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "Bad admin token")


router = APIRouter(prefix="/api", tags=["Operator console"], dependencies=[Depends(require_admin)])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


VERIFIED_LINK_METHODS = ("code_via_trusted_channel", "operator_bootstrap", "operator_dashboard")


def _audit_count(s: Any, *actions: str) -> int:
    return int(s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(actions))) or 0)


@router.get(
    "/overview",
    summary="Overview",
    description="Growth stage, what is live (brain, channels), spend against the cap and the kill switch.",
)
def overview() -> dict[str, Any]:
    settings = get_settings()
    spend = BudgetGuard.for_instance(settings).summary().as_dict()
    day_ago = datetime.now(UTC) - timedelta(days=1)
    with session_scope() as s:
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
            "model_mode": "Claude API (real replies)"
            if brain_ready(settings)
            else "offline (no API key, replies are echoes)",
            "channels": {
                "telegram": bool(settings.telegram_bot_token),
                "slack": bool(settings.slack_bot_token and settings.slack_app_token),
            },
            "simulator": settings.enable_simulator,
            "spend": spend,
            # Gates from Infant to Child. Most are measured from phase 2 onward.
            "next_stage": "Child",
            "gates": [
                {"label": "Onboarding interview done", "status": "later", "detail": "Arrives in phase 2"},
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
                "memories": s.scalar(select(func.count()).select_from(MemoryItem)),
                "pending_approvals": s.scalar(
                    select(func.count()).select_from(Approval).where(Approval.status == "pending")
                ),
                "refused_actions": s.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action.in_(["action_refused", "llm_call_refused"]))
                ),
            },
        }


@router.get(
    "/people",
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


@router.get("/conversations", summary="Conversations", description="Every chat Ava is in, with its audience.")
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
    "/events", summary="Messages", description="Recent messages, optionally for one conversation (`context_id`)."
)
def events(context_id: uuid.UUID | None = None, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    with session_scope() as s:
        q = select(Event, Person.display_name).join(Person, Person.id == Event.resolved_person_id, isouter=True)
        if context_id:
            q = q.where(Event.context_id == context_id)
        rows = s.execute(q.order_by(Event.created_at.desc()).limit(limit)).all()
        return [
            {
                "id": str(e.id),
                "context_id": str(e.context_id),
                "direction": e.direction,
                "sender": name or ("agent" if e.direction == "out" else "stranger"),
                "trust": e.trust_at_time,
                "audience_size": len(e.audience_person_ids or []),
                "audience_has_unknown": e.audience_has_unknown,
                "content": e.content,
                "at": _iso(e.created_at),
            }
            for e, name in rows
        ]


@router.get("/memories", summary="Memories", description="Stored memories and who may see each one.")
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


@router.get("/approvals", summary="Pending approvals", description="Actions waiting for the owner's yes.")
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
    "/audit", summary="Safety log", description="The audit trail: identity checks, refusals, links, spend and sign-ins."
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
    "/kill", summary="Kill switch", description='`{"on": true}` stops all model calls and replies; `false` resumes.'
)
def kill(body: KillBody) -> dict[str, bool]:
    with session_scope() as s:
        set_frozen(s, body.on, actor="dashboard")
    return {"frozen": body.on}


@router.post(
    "/identities/{identity_id}/unlink",
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
        unlink(s, identity, actor="dashboard", reason="unlinked from dashboard")
    return {"status": "unlinked"}


class LinkBody(BaseModel):
    person_id: uuid.UUID


@router.post(
    "/identities/{identity_id}/link",
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
