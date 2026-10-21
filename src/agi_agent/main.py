"""FastAPI app: channel webhooks, admin API and the dev simulator."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agi_agent.api.admin import require_admin
from agi_agent.api.admin import router as admin_router
from agi_agent.audit import audit
from agi_agent.channels import slack, telegram
from agi_agent.channels.base import FakeMessenger, InboundMessage
from agi_agent.config import get_settings
from agi_agent.db.session import session_scope
from agi_agent.pipeline import Agent

log = logging.getLogger(__name__)


class SimulatedMessage(BaseModel):
    provider: str = "telegram"
    sender_id: str
    sender_display: str = ""
    chat_id: str = ""
    chat_kind: str = "dm"
    text: str
    external_sender: bool = False
    tenant_scope: str = ""


def create_app(agent: Agent | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="agi-agent", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        # The dashboard may be opened as localhost or 127.0.0.1; allow both spellings.
        allow_origins=sorted(
            {
                settings.dashboard_origin,
                settings.dashboard_origin.replace("localhost", "127.0.0.1"),
                settings.dashboard_origin.replace("127.0.0.1", "localhost"),
            }
        ),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(admin_router)

    def get_agent() -> Agent:
        nonlocal agent
        if agent is None:
            from agi_agent.factory import build_agent

            agent = build_agent(settings)
        return agent

    from agi_agent.app.routes import make_router

    app.include_router(make_router(get_agent))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request, background: BackgroundTasks) -> dict[str, bool]:
        if not telegram.verify_secret(request.headers.get(telegram.SECRET_HEADER), settings.telegram_webhook_secret):
            with session_scope() as s:
                audit(s, "webhook", "rejected", provider="telegram", reason="bad secret")
            raise HTTPException(401, "bad secret")
        msg = telegram.parse_update(await request.json(), verified=True)
        if msg is not None:
            background.add_task(get_agent().handle, msg)
        return {"ok": True}

    @app.post("/webhooks/slack")
    async def slack_webhook(request: Request, background: BackgroundTasks) -> dict[str, Any]:
        body = await request.body()
        ok = slack.verify_signature(
            settings.slack_signing_secret,
            request.headers.get("X-Slack-Request-Timestamp"),
            body,
            request.headers.get("X-Slack-Signature"),
        )
        if not ok:
            with session_scope() as s:
                audit(s, "webhook", "rejected", provider="slack", reason="bad signature")
            raise HTTPException(401, "bad signature")
        payload = json.loads(body)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}
        msg = slack.parse_event(payload, our_team_id=settings.slack_team_id, verified=True)
        if msg is not None:
            background.add_task(get_agent().handle, msg)
        return {"ok": True}

    @app.post("/api/simulate", dependencies=[Depends(require_admin)])
    def simulate(body: SimulatedMessage) -> dict[str, Any]:
        """Dev only: pretend a message arrived, to try impostor scenarios from
        the dashboard. Replies and link codes are captured, not sent."""
        if not settings.enable_simulator:
            raise HTTPException(403, "Set ENABLE_SIMULATOR=true in .env to use the simulator")
        base = get_agent()
        outbox = FakeMessenger()
        sim = Agent(llm=base.llm, messengers={"telegram": outbox, "slack": outbox}, settings=settings)
        msg = InboundMessage(
            provider=body.provider,
            tenant_scope=body.tenant_scope,
            sender_id=body.sender_id,
            chat_id=body.chat_id or body.sender_id,
            chat_kind=body.chat_kind,
            text=body.text,
            verified=True,
            sender_display=body.sender_display,
            external_sender=body.external_sender,
            raw={"team_id": body.tenant_scope} if body.provider == "slack" else {},
        )
        result = sim.handle(msg)
        with session_scope() as s:
            audit(s, "dashboard", "simulated_message", provider=body.provider, mode=result.mode)
        return {"mode": result.mode, "reply": result.reply, "notes": result.notes, "outbox": outbox.sent}

    # Serve the prebuilt dashboard (web/dist) from the same address, so trying
    # the agent needs no Node and no CORS. API routes above take precedence.
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")

    return app


app = create_app()
