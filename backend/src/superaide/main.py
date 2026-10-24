"""The backend: a JSON API only. The React frontend (web/) runs separately on port 3000."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import superaide.plugins  # noqa: F401  (registers the approval executors and event subscribers)
from superaide import docs
from superaide.api.admin import require_admin
from superaide.api.admin import router as admin_router
from superaide.auth.routes import make_router as make_auth_router
from superaide.channels import slack, telegram
from superaide.channels.base import FakeMessenger, InboundMessage
from superaide.jobs.worker import JobWorker
from superaide.pipeline import Agent
from superaide.platform import envelope
from superaide.platform.audit import audit
from superaide.platform.config import get_settings
from superaide.platform.db.session import session_scope
from superaide.platform.envelope import error_response
from superaide.platform.tenancy.context import default_scope, use_instance
from superaide.platform.tenancy.endpoints import scope_of as endpoint_scope
from superaide.platform.tenancy.endpoints import secret_ok
from superaide.platform.tenancy.models import ChannelEndpoint
from superaide.runtime import Runtime

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
    from superaide.platform.security import logredact

    if settings.surface_check_on_boot:
        from superaide.platform.security import surface

        surface.verify_boot()

    envelope.install_log_context()
    logredact.install()  # uvicorn and the worker log through this too: no bot token in any line
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

    app.include_router(admin_router)
    app.include_router(make_auth_router())

    fixed = agent  # tests pass their own agent; it is never rebuilt
    runtime = Runtime(settings, agent)
    get_agent = runtime.get_agent
    handle_on_server_channels = runtime.handle_on_server_channels

    def apply_connections() -> dict[str, bool]:
        """Use new or removed credentials now: rebuild the agent, and the listeners when this
        process runs them (a server's separate worker follows the changes by itself)."""
        return runtime.apply_connections(listen=settings.run_channels_in_api)

    from superaide.platform.security import dbcheck

    db_gate = dbcheck.Gate()
    app.state.db_gate = db_gate

    @app.middleware("http")
    async def database_ready(request, call_next):
        # While the database is behind the code, every route but /health says so plainly instead of failing
        # halfway with a 500 (security/dbcheck.py).
        message = db_gate.current()
        if message and request.url.path not in {"/health", "/docs", "/redoc", "/openapi.json"}:
            return error_response(
                503, message, code="service_unavailable", detail={"reason": "database_behind"}, legacy_detail=message
            )
        return await call_next(request)

    envelope.install(app)  # added last = outermost: the request id exists before anything else answers

    worker = JobWorker()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        from superaide.brain import platform_ai

        platform_ai.warn_if_unusable()
        if fixed is None:
            from superaide.platform.security import dbcheck, deploy

            deploy.check(settings)
            from superaide.links import warn_if_local

            warn_if_local(settings)  # links in mail point at localhost on a deployment others can reach (#438)
            db_gate.message = dbcheck.check(settings)  # refuses to start when hosted and behind
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
        runtime.stop()

    app.router.lifespan_context = lifespan

    from superaide.app.connections import make_router as make_connections_router
    from superaide.app.routes import make_router

    app.include_router(make_router(get_agent))
    app.include_router(make_connections_router(get_agent, apply_connections))
    from superaide.app.linked import make_router as make_linked_router

    app.include_router(make_linked_router(get_agent))
    from superaide.app.providers import make_router as make_providers_router

    app.include_router(make_providers_router(get_agent, apply_connections))
    from superaide.integrations.gmail.routes import make_router as make_gmail_router

    app.include_router(make_gmail_router(get_agent))
    from superaide.integrations.calendar import settings as calendar_settings
    from superaide.skills.connector_settings import make_router as make_connector_settings_router

    app.include_router(
        make_connector_settings_router(
            prefix="calendar",
            skill_id=calendar_settings.SKILL_ID,
            choices=calendar_settings.CHOICES,
            tag="Owner: Calendar",
            what="Google Calendar reading",
            event="calendar_read_settings",
        )
    )
    from superaide.auth.stepup_routes import make_router as make_stepup_router

    app.include_router(make_stepup_router())
    from superaide.skills import web_settings

    app.include_router(
        make_connector_settings_router(
            prefix="web",
            skill_id=web_settings.SKILL_ID,
            choices=web_settings.CHOICES,
            tag="Owner: Web research",
            what="Web research",
            event="web_read_settings",
        )
    )
    from superaide.triage.routes import make_router as make_triage_router

    app.include_router(make_triage_router(get_agent))  # 404 everywhere while TRIAGE_ENABLED is off
    from superaide.identity.claims_routes import make_router as make_claims_router

    app.include_router(make_claims_router())  # 404 everywhere while IDENTITY_CLAIM_REVIEW is off
    from superaide.features.school.routes import make_router as make_school_router

    app.include_router(make_school_router(get_agent))  # 404 everywhere while SCHOOL_ENABLED is off
    from superaide.reception.routes import make_router as make_reception_router

    app.include_router(make_reception_router(get_agent))  # receptionist (ADR 0010 TR5)
    from superaide.tooltrace.audition_routes import make_router as make_audition_routes_router

    app.include_router(make_audition_routes_router(get_agent))  # 404 everywhere while its flag is off

    from superaide.tooltrace.drill_routes import make_router as make_drill_routes_router

    app.include_router(make_drill_routes_router(get_agent))  # 404 everywhere while its flag is off

    from superaide.tooltrace.preview_routes import make_router as make_preview_routes_router

    app.include_router(make_preview_routes_router(get_agent))  # 404 everywhere while its flag is off
    from superaide.features.rehearsal.routes import make_router as make_rehearsal_router

    app.include_router(make_rehearsal_router(get_agent))  # 404 everywhere while REHEARSAL_ENABLED is off
    from superaide.asklink.routes import make_owner_router as make_ask_owner_router
    from superaide.asklink.routes import make_public_router as make_ask_public_router

    app.include_router(make_ask_public_router())  # 404 everywhere while ASK_LINK_ENABLED is off
    app.include_router(make_ask_owner_router())
    from superaide.principal.routes import make_owner_router as make_card_owner_router
    from superaide.principal.routes import make_public_router as make_card_public_router

    app.include_router(make_card_public_router())  # 404 everywhere while PRINCIPAL_CARD_ENABLED is off
    app.include_router(make_card_owner_router())
    from superaide.features.memsocket.routes import make_owner_router as make_socket_owner_router
    from superaide.features.memsocket.routes import make_public_router as make_socket_public_router

    app.include_router(make_socket_public_router())  # 404 everywhere while MEMORY_SOCKET_ENABLED is off
    app.include_router(make_socket_owner_router())
    from superaide.features.brainshare.routes import make_owner_router as make_share_owner_router
    from superaide.features.brainshare.routes import make_public_router as make_share_public_router

    app.include_router(make_share_public_router())  # 404 everywhere while BRAIN_SHARE_ENABLED is off
    app.include_router(make_share_owner_router())
    from superaide.features.dissent.routes import make_router as make_dissent_router

    app.include_router(make_dissent_router(get_agent))  # 404 unless DISSENT_DESK_ENABLED and three models are set
    from superaide.features.forknight.routes import make_router as make_fork_router

    app.include_router(make_fork_router(get_agent))  # 404 unless FORK_NIGHT_ENABLED
    from superaide.rules.routes import make_router as make_rules_router

    app.include_router(make_rules_router(get_agent))  # 404 unless TIME_TRAVEL_RULES_ENABLED
    from superaide.mandate.routes import make_router as make_mandate_router

    app.include_router(make_mandate_router())  # 404 for the owner unless MANDATES_ENABLED
    from superaide.features.push.routes import make_router as make_push_router

    app.include_router(make_push_router())  # 404 unless PUSH_ENABLED (ADR 0011)
    from superaide.app.brain_map import make_brain_router

    app.include_router(make_brain_router(get_agent))
    from superaide.features.passport.routes import make_passport_router

    app.include_router(make_passport_router(get_agent))
    from superaide.features.journal.routes import make_journal_router

    app.include_router(make_journal_router(get_agent))
    from superaide.features.mirror.routes import make_mirror_router

    app.include_router(make_mirror_router(get_agent))  # 404 unless MIRROR_ENABLED
    from superaide.app.episodes import make_episodes_router

    app.include_router(make_episodes_router(get_agent))  # 404 unless SLEEP_JOB_ENABLED

    from superaide.skills.releases_routes import make_skill_releases_router

    app.include_router(make_skill_releases_router(get_agent))  # 404 unless SKILL_COOLDOWN_ENABLED (#129)
    from superaide.skills.signals_routes import make_skill_signals_router

    app.include_router(make_skill_signals_router(get_agent))  # 404 unless SKILL_SIGNALS_ENABLED (#130)
    from superaide.skills.receipts_routes import make_skill_receipts_router

    app.include_router(make_skill_receipts_router(get_agent))  # 404 unless SKILL_RECEIPTS_ENABLED (#131)
    from superaide.skills.payouts_routes import make_skill_payouts_router

    app.include_router(make_skill_payouts_router(get_agent))  # 404 unless PAYOUTS_ENABLED (#132)
    from superaide.features.voice.routes import make_router as make_voice_router

    app.include_router(make_voice_router(get_agent))  # 404 unless VOICE_INTRO_V2 (ADR 0012)
    from superaide.features.imports.routes import make_router as make_import_router

    app.include_router(make_import_router())  # 404 unless IMPORT_LEASE_ENABLED (#133)

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
        description=(
            'Returns `{"status": "ok"}` while the API is up. `{"status": "degraded", "database": "behind"}` means '
            "the database has not been migrated to what the code expects."
        ),
    )
    def health() -> dict[str, str]:
        message = db_gate.current()
        if message:  # still answers 200 (a container check should not kill a server waiting for a migration)
            return {"status": "degraded", "database": "behind", "detail": message}
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
        "/webhooks/google/calendar",
        tags=["Webhooks"],
        summary="Google Calendar push",
        description=(
            "Google calls this when an owner's calendar changes (events.watch). Each channel carries its own "
            "token in `X-Goog-Channel-Token`; anything else is refused and audited. `404` while "
            "GOOGLE_CALENDAR_PUSH is off. The half-hourly scan runs either way."
        ),
    )
    def google_calendar_webhook(request: Request) -> dict[str, bool]:
        from superaide.integrations.calendar import watch as calendar_watch

        if not settings.google_calendar_push:
            raise HTTPException(404, "Not Found")
        h = request.headers
        ok = calendar_watch.handle_notification(
            h.get("x-goog-channel-id"),
            h.get("x-goog-channel-token"),
            h.get("x-goog-resource-id"),
            h.get("x-goog-resource-state"),
        )
        if not ok:
            with session_scope() as s:
                audit(s, "webhook", "rejected", provider="google_calendar", reason="bad channel or token")
            raise HTTPException(401, "bad channel")
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
