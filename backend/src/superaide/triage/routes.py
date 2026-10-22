"""The owner's triage API (ADR 0010 TR7): the Needs-you inbox, acting on an item, feedback,
triage settings, away and busy.

Owner-only (the signed-in account's own instance, `owner_person_id`), CSRF-checked on every
change, and every query is filtered to the instance in scope as well as by Row-Level Security.
With TRIAGE_ENABLED off, every route answers the signed-in owner 404 (others get 401/403 as usual),
so the app hides the inbox.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from superaide.app.routes import owner_person_id
from superaide.auth.routes import reject_nul_text, require_csrf_header
from superaide.db.models import Person
from superaide.db.session import require, session_scope
from superaide.tenancy.context import scope_or_default
from superaide.triage import escalate
from superaide.triage.models import Escalation, EscalationDelivery, TriageDecision, TriageSettings
from superaide.triage.store import current_stage, settings_for
from superaide.triage.types import AWAY_VISIBILITY, Decision, EscalationState, Feedback

WINDOW = timedelta(days=7)
LIMIT = 100
HIDDEN_QUOTES = escalate.QUOTE_HIDDEN
NEEDS_YOU = {Decision.INTERRUPT, Decision.DEFER, Decision.VERIFY}
CLOSED = {EscalationState.RESOLVED, EscalationState.EXPIRED}

Verb = Literal["ack", "reply", "call", "not_urgent", "snooze_30m", "snooze_2h", "done"]


class ActionBody(BaseModel):
    verb: Verb
    request_id: str = Field(default="", max_length=120)  # makes a double-click one ack


class FeedbackBody(BaseModel):
    feedback: Feedback


class Rung(BaseModel):
    provider: Literal["app", "interrupt", "other", "telegram", "slack"]
    delay_s: int = Field(ge=0, le=3600)


class SettingsBody(BaseModel):
    ladder: list[Rung] | None = Field(default=None, min_length=1, max_length=4)
    expire_after_s: int | None = Field(default=None, ge=60, le=86_400)
    max_per_hour: int | None = Field(default=None, ge=0, le=60)
    max_per_day: int | None = Field(default=None, ge=0, le=200)
    vip_ignores_budget: Literal["critical", "always", "never"] | None = None
    theta_interrupt: float | None = Field(default=None, ge=0.4, le=0.85)
    theta_handle: float | None = Field(default=None, ge=0.5, le=1.0)
    silenced_people: list[uuid.UUID] | None = Field(default=None, max_length=500)
    silenced_topics: list[str] | None = Field(default=None, max_length=100)
    autonomy_grants: list[str] | None = Field(default=None, max_length=50)
    stranger_intake: bool | None = None
    away_visibility: Literal["everyone", "colleagues", "nobody"] | None = None
    tell_sender_seen: bool | None = None


class AwayBody(BaseModel):
    until: datetime | None = None  # None ends away
    visibility: Literal["everyone", "colleagues", "nobody"] | None = None


class BusyBody(BaseModel):
    minutes: int = Field(ge=0, le=24 * 60)  # 0 ends busy


def settings_view(ts: TriageSettings) -> dict[str, Any]:
    return {
        "ladder": ts.ladder, "expire_after_s": ts.expire_after_s, "max_per_hour": ts.max_per_hour,
        "max_per_day": ts.max_per_day, "vip_ignores_budget": ts.vip_ignores_budget,
        "theta_interrupt": ts.theta_interrupt, "theta_handle": ts.theta_handle,
        "silenced_people": [str(p) for p in ts.silenced_people or []],
        "silenced_topics": list(ts.silenced_topics or []),
        "autonomy_grants": list(ts.autonomy_grants or []), "stranger_intake": ts.stranger_intake,
        "away_until": ts.away_until.isoformat() if ts.away_until else None, "away_visibility": ts.away_visibility,
        "busy_until": ts.busy_until.isoformat() if ts.busy_until else None, "tell_sender_seen": ts.tell_sender_seen,
        "phone_enabled": ts.phone_enabled,
    }  # fmt: skip


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def item_view(s: Any, d: TriageDecision, esc: Escalation | None) -> dict[str, Any]:
    sender = s.get(Person, d.sender_person_id) if d.sender_person_id else None
    flags = set((d.classifier or {}).get("risk_flags") or [])
    quotes = [] if flags & HIDDEN_QUOTES else list((d.classifier or {}).get("evidence") or [])[:3]
    email = (d.features or {}).get("provider") == "email"
    meta: dict[str, Any] = {}
    if email:
        from superaide.db.models import Event
        from superaide.skills.gmail_inbox import quotable

        event = s.get(Event, d.inbound_event_id)
        meta = dict(event.meta or {}) if event is not None else {}
        quotes = quotes if quotable(d, str(meta.get("subject") or "")) else []
    out: dict[str, Any] = {
        "id": str(d.id), "decision": d.decision, "reason": d.reason,
        "sender": sender.display_name if sender else str(meta.get("from_name") or ""),
        "source": "email" if email else "message",
        "subject": str(meta.get("subject") or "") if email and quotable(d, str(meta.get("subject") or "")) else None,
        "quotes": quotes, "rule_hits": list(d.rule_hits or []), "stage": d.stage_at_time,
        "maybe_urgent": bool((d.features or {}).get("maybe_urgent")),
        "shadow_decision": (d.features or {}).get("shadow_decision"), "feedback": d.feedback,
        "created_at": d.created_at.isoformat(), "escalation": None,
    }  # fmt: skip
    if esc is not None:
        rows = s.scalars(
            select(EscalationDelivery)
            .where(EscalationDelivery.escalation_id == esc.id)
            .order_by(EscalationDelivery.created_at)
        )
        out["escalation"] = {
            "id": str(esc.id), "state": esc.state, "step": esc.step, "cycle": esc.cycle, "next_at": _iso(esc.next_at),
            "acked_at": _iso(esc.acked_at), "acked_via": esc.acked_via, "resolution": esc.resolution,
            "deliveries": [{"step": r.step, "cycle": r.cycle, "provider": r.provider, "status": r.status,
                            "sent_at": _iso(r.sent_at)} for r in rows],
        }  # fmt: skip
    return out


def bucket(d: TriageDecision, esc: Escalation | None) -> str:
    """needs_you | handled | held"""
    if d.decision == Decision.HANDLE:
        return "handled"
    maybe = bool((d.features or {}).get("maybe_urgent")) and d.decision == Decision.HOLD
    if (d.decision in NEEDS_YOU or maybe) and d.feedback is None and (esc is None or esc.state not in CLOSED):
        return "needs_you"
    return "held"


def make_router(get_agent: Callable[[], Any]) -> APIRouter:
    async def owner_on(person_id: uuid.UUID = Depends(owner_person_id)) -> uuid.UUID:
        """The owner, once triage is known to be on. Checked after CSRF and sign-in, so a caller who
        may not use the route gets 401/403 as everywhere else, and only the owner learns it's off (404)."""
        if not get_agent().settings.triage_enabled:
            raise HTTPException(404, "Not Found")
        return person_id

    router = APIRouter(
        prefix="/app",
        tags=["Owner: needs you"],
        dependencies=[Depends(require_csrf_header), Depends(reject_nul_text)],
    )

    def _decision(s: Any, decision_id: uuid.UUID) -> tuple[TriageDecision, Escalation | None]:
        d = s.scalar(
            select(TriageDecision).where(
                TriageDecision.id == decision_id, TriageDecision.agent_id == scope_or_default().agent_id
            )
        )
        if d is None:
            raise HTTPException(404, "Not found")
        return d, s.scalar(select(Escalation).where(Escalation.decision_id == d.id))

    @router.get(
        "/needs-you",
        summary="The Needs-you inbox",
        description="Interrupts and unverified requests that need you, what Ava handled, and what she held.",
    )
    def needs_you(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            since = datetime.now(UTC) - WINDOW
            rows = s.scalars(
                select(TriageDecision)
                .where(TriageDecision.agent_id == scope_or_default().agent_id, TriageDecision.created_at >= since)
                .order_by(TriageDecision.created_at.desc())
                .limit(LIMIT * 3)
            ).all()
            escs = {
                e.decision_id: e
                for e in s.scalars(select(Escalation).where(Escalation.decision_id.in_([d.id for d in rows])))
            }
            lists: dict[str, list[dict[str, Any]]] = {"needs_you": [], "handled": [], "held": []}
            for d in rows:
                where = lists[bucket(d, escs.get(d.id))]
                if len(where) < LIMIT:
                    where.append(item_view(s, d, escs.get(d.id)))
            from superaide.asklink import service as ask
            from superaide.asklink.routes import open_requests

            return {
                **lists,
                "counts": {k: len(v) for k, v in lists.items()},
                "stage": str(current_stage(s)),
                # Requests from the owner's Ask link (source "ask_link"), ranked by the same rules.
                "ask_link": open_requests(s) if ask.enabled() else [],
            }

    @router.post(
        "/needs-you/{decision_id}/action",
        summary="Act on an item",
        description="Ack, reply, call, snooze (30m or 2h), not urgent, or done. Opening the app is never an ack.",
    )
    def act(decision_id: uuid.UUID, body: ActionBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            me = require(s, Person, person_id)
            d, esc = _decision(s, decision_id)
            changed = False
            if esc is not None:
                try:
                    changed = escalate.ack(s, esc.id, source="app", external_id=body.request_id or uuid.uuid4().hex,
                                           verb=body.verb, actor=me)  # fmt: skip
                except escalate.AckRefused as exc:
                    raise HTTPException(404, "Not found") from exc
            elif body.verb.startswith("snooze"):
                raise HTTPException(409, "Only an escalation can be snoozed")
            elif d.feedback is None:  # an unverified request or a "maybe urgent": closing it is the feedback
                fb = Feedback.NOT_URGENT if body.verb == "not_urgent" else Feedback.USEFUL
                escalate.record_feedback(s, d, fb, via="app")
                changed = True
            s.flush()
            if esc is not None:
                s.refresh(esc)
            return {"changed": changed, "item": item_view(s, d, esc)}

    @router.post(
        "/needs-you/{decision_id}/feedback",
        summary="Tell Ava how she did",
        description="Not urgent, you should have told me, good or wrong answer. It tunes rules, not prompts.",
    )
    def feedback(
        decision_id: uuid.UUID, body: FeedbackBody, person_id: uuid.UUID = Depends(owner_on)
    ) -> dict[str, Any]:
        with session_scope() as s:
            d, esc = _decision(s, decision_id)
            escalate.record_feedback(s, d, body.feedback, via="app")
            return {"item": item_view(s, d, esc)}

    @router.get("/triage/settings", summary="Triage settings")
    def get_settings_(person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            return settings_view(settings_for(s, require(s, Person, person_id)))

    @router.put(
        "/triage/settings",
        summary="Change triage settings",
        description="Only the fields sent change. The defaults are proposals waiting for your answers.",
    )
    def put_settings(body: SettingsBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            ts = settings_for(s, require(s, Person, person_id))
            changes = body.model_dump(exclude_none=True)
            if "ladder" in changes:
                changes["ladder"] = [dict(r) for r in changes["ladder"]]
            if "silenced_topics" in changes:
                changes["silenced_topics"] = [t.strip()[:120] for t in changes["silenced_topics"] if t.strip()]
            if "autonomy_grants" in changes:
                changes["autonomy_grants"] = [g.strip()[:60] for g in changes["autonomy_grants"] if g.strip()]
            for k, v in changes.items():
                setattr(ts, k, v)
            return settings_view(ts)

    @router.post("/triage/away", summary="Away", description="Set or end away mode, and who may be told.")
    def away(body: AwayBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            ts = settings_for(s, require(s, Person, person_id))
            if body.until is not None and body.until.tzinfo is None:
                raise HTTPException(422, "Give a time zone with the time")
            ts.away_until = body.until
            if body.visibility in AWAY_VISIBILITY:
                ts.away_visibility = body.visibility
            return settings_view(ts)

    @router.post(
        "/triage/busy", summary="Busy", description="Hold non-VIP interrupts for this many minutes (0 ends it)."
    )
    def busy(body: BusyBody, person_id: uuid.UUID = Depends(owner_on)) -> dict[str, Any]:
        with session_scope() as s:
            ts = settings_for(s, require(s, Person, person_id))
            ts.busy_until = datetime.now(UTC) + timedelta(minutes=body.minutes) if body.minutes else None
            return settings_view(ts)

    return router
