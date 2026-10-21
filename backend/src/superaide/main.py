"""The backend: a JSON API only. The React frontend (web/) runs separately on port 3000."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from superaide.api.admin import require_admin
from superaide.api.admin import router as admin_router
from superaide.audit import audit
from superaide.auth.routes import make_router as make_auth_router
from superaide.channels import slack, telegram
from superaide.channels.base import FakeMessenger, InboundMessage
from superaide.channels.runner import ChannelRunner
from superaide.config import get_settings
from superaide.db.session import session_scope
from superaide.pipeline import Agent

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
    app = FastAPI(title="Superaide API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        # The frontend may be opened as localhost or 127.0.0.1; allow both spellings.
        allow_origins=sorted(
            {
                settings.frontend_origin,
                settings.frontend_origin.replace("localhost", "127.0.0.1"),
                settings.frontend_origin.replace("127.0.0.1", "localhost"),
            }
        ),
        allow_credentials=True,  # the session cookie travels with the frontend's requests
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's own 422, minus the "input" it echoes back: that can be a password or an API key
        (for a missing field it is the whole body, other credentials included)."""
        errors = [{k: v for k, v in error.items() if k != "input"} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    app.include_router(admin_router)
    app.include_router(make_auth_router())

    fixed = agent  # tests pass their own agent; it is never rebuilt
    current: dict[str, Any] = {"agent": None, "version": None}

    def get_agent() -> Agent:
        """The agent, rebuilt whenever a connection (Claude, Telegram, Slack) changes in the app."""
        if fixed is not None:
            return fixed
        from superaide.factory import build_agent
        from superaide.vault.store import runtime_settings, version

        with session_scope() as s:
            v = version(s)
        if current["agent"] is None or current["version"] != v:
            current["agent"], current["version"] = build_agent(runtime_settings(settings)), v
        return current["agent"]

    runner = ChannelRunner(lambda msg: get_agent().handle(msg))

    def apply_connections() -> dict[str, bool]:
        """Use new or removed credentials now: rebuild the agent and the listeners."""
        a = get_agent()
        if fixed is not None or not settings.run_channels_in_api:
            return {}
        return runner.ensure(a.settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if fixed is None and settings.run_channels_in_api:
            try:
                started = apply_connections()
                log.info("channel listeners: %s", started)
            except Exception:
                log.exception("couldn't start channel listeners")
        yield
        runner.stop()

    app.router.lifespan_context = lifespan

    from superaide.app.connections import make_router as make_connections_router
    from superaide.app.routes import make_router

    app.include_router(make_router(get_agent))
    app.include_router(make_connections_router(get_agent, apply_connections))

    @app.get("/")
    def index() -> dict[str, str]:
        return {
            "service": "superaide API",
            "frontend": settings.frontend_origin,
            "docs": "/docs",
        }

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

    return app


app = create_app()
