"""The backend: a JSON API only. The React frontend (web/) runs separately on port 3000."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from superaide import docs
from superaide.api.admin import require_admin
from superaide.api.admin import router as admin_router
from superaide.audit import audit
from superaide.auth.routes import make_router as make_auth_router
from superaide.channels import slack, telegram
from superaide.channels.base import FakeMessenger, InboundMessage
from superaide.channels.runner import ChannelRunner
from superaide.config import get_settings
from superaide.db.session import session_scope
from superaide.jobs.worker import JobWorker
from superaide.pipeline import Agent
from superaide.tenancy.context import current_scope, default_scope, scope_or_default, use_instance
from superaide.tenancy.endpoints import owner_of, secret_ok, sync_telegram, webhook_secret
from superaide.tenancy.endpoints import scope_of as endpoint_scope
from superaide.tenancy.models import ChannelEndpoint

log = logging.getLogger(__name__)
MAX_CACHED = 256  # agents kept built, one per recently active instance


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
    app = FastAPI(
        title="Superaide API",
        version="0.1.0",
        description=docs.DESCRIPTION,
        openapi_tags=docs.TAGS,
        swagger_ui_parameters=docs.SWAGGER_UI,
        # Hidden on a public server (ENABLE_API_DOCS=false): it maps every endpoint for a visitor.
        docs_url="/docs" if settings.enable_api_docs else None,
        redoc_url="/redoc" if settings.enable_api_docs else None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
    )
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
    # One agent per hired instance (ADR 0009 T3), each built from that instance's own connections and
    # rebuilt when they change. Least recently used ones are dropped beyond MAX_CACHED.
    current: OrderedDict[Any, tuple[Agent, str]] = OrderedDict()
    cache_lock = threading.Lock()

    def get_agent() -> Agent:
        """The agent of the instance in scope, rebuilt whenever its connections change in the app."""
        if fixed is not None:
            return fixed
        from superaide.factory import build_agent
        from superaide.vault.store import runtime_settings, version

        key = scope_or_default().agent_id
        with session_scope() as s:
            v = version(s)
        with cache_lock:
            cached = current.get(key)
            if cached is not None and cached[1] == v:
                current.move_to_end(key)
                return cached[0]
        built = build_agent(runtime_settings(settings))
        with cache_lock:
            current[key] = (built, v)
            current.move_to_end(key)
            while len(current) > MAX_CACHED:
                current.popitem(last=False)
        return built

    def handle_on_server_channels(msg: InboundMessage) -> Any:
        """Slack (one app per server for now) and the server's legacy Telegram webhook belong to the
        server's first instance."""
        with use_instance(default_scope()):
            return get_agent().handle(msg)

    def handle_listened(msg: InboundMessage) -> Any:
        """From a listener: an owner bot's poller runs inside its owner's instance (ADR 0009 T4)."""
        if current_scope() is not None:
            return get_agent().handle(msg)
        return handle_on_server_channels(msg)

    runner = ChannelRunner(handle_listened)
    hooked: dict[str, Any] = {}  # bot token -> instance whose webhook Telegram already has

    def apply_connections() -> dict[str, bool]:
        """Use new or removed credentials now: rebuild the agent and the listeners."""
        if fixed is not None or not settings.run_channels_in_api:
            get_agent()
            return {}
        with use_instance(default_scope()):  # Slack follows the first instance's connections
            base = get_agent().settings
            started = runner.ensure(base.model_copy(update={"telegram_bot_token": ""}))
        bots = sync_telegram()  # each owner's own bot, registered to their instance
        if settings.telegram_webhook_base_url:
            runner.ensure_bots({})
            for token, scope in bots.items():
                if hooked.get(token) != scope:
                    with use_instance(scope), session_scope() as s:
                        endpoint = owner_of(s, "telegram", telegram.bot_id(token))
                    if endpoint is not None and telegram.set_webhook(
                        token,
                        f"{settings.telegram_webhook_base_url.rstrip('/')}/webhooks/telegram/{endpoint.id}",
                        webhook_secret(endpoint.id),
                    ):
                        hooked[token] = scope
            started["telegram"] = bool(bots)
        else:
            started["telegram"] = bool(runner.ensure_bots(bots))
        return started

    worker = JobWorker()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if fixed is None and settings.run_channels_in_api:
            try:
                started = apply_connections()
                log.info("channel listeners: %s", started)
            except Exception:
                log.exception("couldn't start channel listeners")
        if fixed is None and settings.run_jobs_in_api:
            try:
                from superaide.context import vectors

                with session_scope() as s:
                    vectors.start(s)  # load the local embedder in the background; backfill once per model
            except Exception:
                log.exception("couldn't start embeddings; recall stays keyword-only")
            worker.start()
        yield
        worker.stop()
        runner.stop()

    app.router.lifespan_context = lifespan

    from superaide.app.connections import make_router as make_connections_router
    from superaide.app.routes import make_router

    app.include_router(make_router(get_agent))
    app.include_router(make_connections_router(get_agent, apply_connections))
    from superaide.app.linked import make_router as make_linked_router

    app.include_router(make_linked_router(get_agent))
    from superaide.app.providers import make_router as make_providers_router

    app.include_router(make_providers_router(get_agent, apply_connections))
    from superaide.triage.routes import make_router as make_triage_router

    app.include_router(make_triage_router(get_agent))  # 404 everywhere while TRIAGE_ENABLED is off

    @app.get(
        "/",
        tags=["Health"],
        summary="About this API",
        description="Points to the docs. Handy as a first check that the server answers.",
    )
    def index() -> dict[str, str]:
        return {
            "service": "superaide API",
            "frontend": settings.frontend_origin,
            "docs": "/docs",
        }

    @app.get(
        "/health",
        tags=["Health"],
        summary="Health check",
        description='Returns `{"status": "ok"}` while the API is up.',
    )
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/webhooks/telegram",
        tags=["Webhooks"],
        summary="Telegram webhook",
        description=(
            "Telegram delivers updates here when a webhook is set. Requests without the right `X-Telegram-Bot-Api-"
            "Secret-Token` are dropped and audited. Local setups use polling instead."
        ),
    )
    async def telegram_webhook(request: Request, background: BackgroundTasks) -> dict[str, bool]:
        if not telegram.verify_secret(request.headers.get(telegram.SECRET_HEADER), settings.telegram_webhook_secret):
            with session_scope() as s:
                audit(s, "webhook", "rejected", provider="telegram", reason="bad secret")
            raise HTTPException(401, "bad secret")
        with use_instance(default_scope()):  # the server's bot belongs to its first instance (until T4)
            bot = telegram.bot_id(get_agent().settings.telegram_bot_token)
        update = await request.json()
        msg = telegram.parse_update(update, verified=True, bot=bot)
        if msg is not None:
            background.add_task(handle_on_server_channels, msg)
        elif (press := telegram.parse_callback(update, verified=True, bot=bot)) is not None:

            def press_in_scope(p: Any = press) -> None:  # escalation buttons (ADR 0010)
                with use_instance(default_scope()):
                    telegram._handle_press(get_agent().settings.telegram_bot_token, p)

            background.add_task(press_in_scope)
        return {"ok": True}

    @app.post(
        "/webhooks/telegram/{endpoint_id}",
        tags=["Webhooks"],
        summary="Telegram webhook for one owner's bot",
        description=(
            "Each owner's own bot delivers here, at its own endpoint, with that endpoint's secret in "
            "`X-Telegram-Bot-Api-Secret-Token`. The update is handled by that owner's Ava only."
        ),
    )
    async def telegram_endpoint_webhook(
        endpoint_id: uuid.UUID, request: Request, background: BackgroundTasks
    ) -> dict[str, bool]:
        with session_scope() as s:
            endpoint = s.get(ChannelEndpoint, endpoint_id)
            known = endpoint is not None and endpoint.status == "active" and endpoint.provider == "telegram"
            scope = endpoint_scope(endpoint) if known and endpoint else None
            bot = endpoint.external_key if known and endpoint else ""
        if scope is None or not secret_ok(endpoint_id, request.headers.get(telegram.SECRET_HEADER)):
            with session_scope() as s:
                audit(s, "webhook", "rejected", provider="telegram", reason="bad endpoint or secret")
            raise HTTPException(401, "bad secret")
        update = await request.json()
        msg = telegram.parse_update(update, verified=True, bot=bot)
        if msg is not None:

            def handle_in_scope(m: InboundMessage = msg, sc: Any = scope) -> Any:
                with use_instance(sc):
                    return get_agent().handle(m)

            background.add_task(handle_in_scope)
        elif (press := telegram.parse_callback(update, verified=True, bot=bot)) is not None:

            def press_in_scope(p: Any = press, sc: Any = scope) -> None:  # escalation buttons (ADR 0010)
                from superaide.vault.store import runtime_settings

                with use_instance(sc):
                    telegram._handle_press(runtime_settings().telegram_bot_token, p)

            background.add_task(press_in_scope)
        return {"ok": True}

    @app.post(
        "/webhooks/slack",
        tags=["Webhooks"],
        summary="Slack events",
        description=(
            "Slack's Events API. The request signature and timestamp are checked; `url_verification` challenges are "
            "answered. Local setups use Socket Mode instead."
        ),
    )
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
            background.add_task(handle_on_server_channels, msg)
        return {"ok": True}

    @app.post(
        "/api/simulate",
        dependencies=[Depends(require_admin)],
        tags=["Simulator"],
        summary="Send a simulated message",
        description=(
            "Runs a message through the full pipeline as if it came from the given account, and returns what Ava "
            "would reply. Needs the admin token and `ENABLE_SIMULATOR=true`."
        ),
    )
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
