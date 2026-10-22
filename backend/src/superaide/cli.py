"""Command line: `uv run superaide <command>`."""

from __future__ import annotations

import argparse
import logging
import sys
import uuid

from superaide.config import get_settings


def _init_db(_: argparse.Namespace) -> None:
    """Create tables directly (quick start). For real deployments use `alembic upgrade head`."""
    from superaide.context.vectors import ensure_vector_store
    from superaide.db.models import Base
    from superaide.db.session import ensure_extensions, get_engine

    engine = get_engine(get_settings().owner_database_url)
    ensure_extensions(engine)
    Base.metadata.create_all(engine)
    ensure_vector_store(engine)
    print("Tables created.")


def _db_app_role(args: argparse.Namespace) -> None:
    """Create or update the app's own database role (no superuser, no BYPASSRLS). Run as the owner."""
    import os

    from superaide.db.roles import RoleError, ensure_app_role
    from superaide.db.session import get_engine

    password = os.environ.get(args.password_env, "")
    if not password:
        sys.exit(f"Set {args.password_env} to the password the app will use.")
    try:
        with get_engine(get_settings().owner_database_url).begin() as conn:
            ensure_app_role(conn, name=args.name, password=password)
    except RoleError as exc:
        sys.exit(str(exc))
    print(f"Role {args.name} is ready. Point DATABASE_URL at it, and MIGRATION_DATABASE_URL at the owner.")


def _add_person(args: argparse.Namespace) -> None:
    from superaide.db.session import session_scope
    from superaide.identity.linking import bootstrap_person

    settings = get_settings()
    identities: list[tuple[str, str, str]] = []
    if args.telegram_id:
        identities.append(("telegram", "", str(args.telegram_id)))
    if args.slack_user:
        team = args.slack_team or settings.slack_team_id
        if not team:
            sys.exit("Give --slack-team or set SLACK_TEAM_ID in .env")
        identities.append(("slack", team, args.slack_user))
    if not identities:
        sys.exit("Give at least one of --telegram-id or --slack-user")
    with session_scope() as s:
        person = bootstrap_person(s, name=args.name, relation=args.relation, identities=identities)
        print(f"Added {args.name} ({args.relation}) with {len(identities)} channel(s). id={person.id}")


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    # Bind to localhost only: the admin API must not be reachable from outside.
    uvicorn.run("superaide.main:app", host="127.0.0.1", port=args.port, reload=args.reload)


def _worker(args: argparse.Namespace) -> None:
    """Run the background work of every hired Ava in this process, instead of inside the API (ADR 0009
    T11): jobs (reminders, digests, learning) and the Telegram/Slack listeners. It follows connections
    saved in the app every `--refresh` seconds. Several workers can run: jobs are claimed with row
    locks, and each bot is listened to by one process only (an advisory lock per bot)."""
    import signal
    import threading

    from superaide.db.session import session_scope
    from superaide.jobs.worker import JobWorker
    from superaide.runtime import Runtime

    settings = get_settings()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runtime = Runtime(settings)
    jobs = JobWorker()
    try:
        from superaide.context import vectors

        with session_scope() as s:
            vectors.start(s)  # the local embedder, in the background
    except Exception:
        logging.getLogger(__name__).exception("couldn't start embeddings; recall stays keyword-only")
    if not args.no_jobs:
        jobs.start()
    log = logging.getLogger("superaide.worker")
    log.info("worker started (jobs: %s, listeners: %s)", not args.no_jobs, not args.no_listeners)
    try:
        while not stop.is_set():
            if not args.no_listeners:
                try:
                    log.debug("listeners: %s", runtime.apply_connections(listen=True))
                except Exception:
                    log.exception("couldn't refresh the listeners; trying again shortly")
            stop.wait(args.refresh)
    finally:
        jobs.stop()
        runtime.stop()
        log.info("worker stopped")


def _telegram_poll(_: argparse.Namespace) -> None:
    from superaide.channels.telegram import poll_forever
    from superaide.factory import build_agent

    settings = get_settings()
    if not settings.telegram_bot_token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env")
    poll_forever(settings.telegram_bot_token, build_agent(settings).handle)


def _slack_socket(_: argparse.Namespace) -> None:
    from superaide.channels.slack import run_socket_mode
    from superaide.factory import build_agent

    settings = get_settings()
    if not (settings.slack_bot_token and settings.slack_app_token):
        sys.exit("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env")
    run_socket_mode(
        settings.slack_bot_token, settings.slack_app_token, settings.slack_team_id, build_agent(settings).handle
    )


def _freeze(args: argparse.Namespace) -> None:
    from superaide.db.session import session_scope
    from superaide.policy.killswitch import set_platform_frozen

    with session_scope() as s:
        set_platform_frozen(s, args.on, actor="cli")  # every hired Ava on this server
    print("Agent frozen: no model calls, no replies." if args.on else "Agent running.")


def _spend(_: argparse.Namespace) -> None:
    from superaide.brain.budget import BudgetGuard

    summary = BudgetGuard.for_instance(get_settings()).summary()
    print(
        f"Spent US${summary.settled_usd:.4f} of US${summary.cap_usd:.2f} "
        f"({summary.calls} calls, US${summary.reserved_usd:.4f} reserved, US${summary.remaining_usd:.4f} left)"
    )


def _push_keys(_: argparse.Namespace) -> None:
    """Show the web push public key, making the pair once if PUSH_ENABLED is on (ADR 0011). Never rotates."""
    from superaide.db.session import session_scope
    from superaide.push import vapid

    with session_scope() as s:
        keys = vapid.ensure(s)
    if keys is None:
        print("Push is off (PUSH_ENABLED=false), so no VAPID keys were made.")
        sys.exit(1)
    print(f"VAPID public key: {keys.public_key}")


def _unlink(args: argparse.Namespace) -> None:
    from superaide.db.models import ChannelIdentity
    from superaide.db.session import session_scope
    from superaide.identity.linking import unlink

    try:
        identity_id = uuid.UUID(args.identity)
    except ValueError:
        sys.exit(f"{args.identity!r} is not an identity id (expected a UUID, as shown in the console's People page)")
    with session_scope() as s:
        identity = s.get(ChannelIdentity, identity_id)
        if identity is None or identity.person_id is None:
            sys.exit("No linked identity with that id")
        unlink(s, identity, actor="cli", reason=args.reason)
    print("Unlinked.")


def _hello(_: argparse.Namespace) -> None:
    """One tiny real Claude call (Haiku, ~40 output tokens) to check the key and the ledger."""
    from superaide.brain.budget import BudgetGuard
    from superaide.brain.llm import ClaudeLLM, SystemBlock

    settings = get_settings()
    llm = ClaudeLLM(BudgetGuard(settings.max_spend_usd), api_key=settings.anthropic_api_key)
    result = llm.complete(
        purpose="smoke_test",
        model=settings.model_triage,
        system=[SystemBlock("Reply in one short sentence.")],
        messages=[{"role": "user", "content": "Say hello to the superaide project."}],
        max_tokens=1000,  # the model thinks first, and that counts against max_tokens
        effort="low",
    )
    print(result.text)
    print(f"Cost: US${result.cost_usd:.6f}")
    _spend(_)


def _seed_demo(_: argparse.Namespace) -> None:
    """Add demo people so the dashboard and impostor simulator have someone to protect.
    Does nothing if anyone is already registered."""
    from sqlalchemy import func, select

    from superaide.db.models import Person
    from superaide.db.session import session_scope
    from superaide.identity.linking import bootstrap_person

    team = get_settings().slack_team_id or "T_DEMO"
    with session_scope() as s:
        if s.scalar(select(func.count()).select_from(Person)):
            print("People already registered; demo data not added.")
            return
        bootstrap_person(
            s,
            name="Shashank",
            relation="manager",
            identities=[("telegram", "", "demo-1001"), ("slack", team, "U_DEMO_SHASHANK")],
            actor="demo_seed",
        )
        bootstrap_person(
            s, name="Priya Tan", relation="colleague", identities=[("slack", team, "U_DEMO_PRIYA")], actor="demo_seed"
        )
    print("Added demo people: Shashank (manager) and Priya Tan (colleague).")


def _reset_password(args: argparse.Namespace) -> None:
    """Set a new password for an account from the terminal (there's no email reset yet)."""
    import getpass

    from superaide.auth.service import AuthError, reset_password
    from superaide.db.session import session_scope

    first = getpass.getpass("New password (10+ characters): ")
    if getpass.getpass("Type it again: ") != first:
        sys.exit("The two passwords don't match. Nothing changed.")
    try:
        with session_scope() as s:
            user = reset_password(s, args.email, first)
            name = user.display_name
    except AuthError as exc:
        sys.exit(exc.message)
    print(f"Password changed for {name}. They've been signed out everywhere; log in again at the app.")


def _reset_db(args: argparse.Namespace) -> None:
    """Delete every table. Run `alembic upgrade head` afterwards."""
    from sqlalchemy import text

    from superaide.db.models import Base
    from superaide.db.session import get_engine

    if not args.yes:
        sys.exit("This deletes all data. Run again with --yes to confirm.")
    engine = get_engine(get_settings().owner_database_url)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS memory_embedding"))  # outside the ORM metadata
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    print("Database wiped.")


def main() -> None:
    from superaide.security import logredact

    logredact.install()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="superaide")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="create tables").set_defaults(func=_init_db)
    p = sub.add_parser("db-app-role", help="create or update the app's own database role (run after migrations)")
    p.add_argument("--name", default="superaide_app")
    p.add_argument("--password-env", default="APP_DB_PASSWORD", help="environment variable holding its password")
    p.set_defaults(func=_db_app_role)

    for name, relation in (("add-manager", "manager"), ("add-colleague", "colleague")):
        p = sub.add_parser(name, help=f"register a {relation} and their first channels")
        p.add_argument("--name", required=True)
        p.add_argument("--telegram-id", help="numeric Telegram user ID")
        p.add_argument("--slack-user", help="Slack user ID, e.g. U0123ABC")
        p.add_argument("--slack-team", help="Slack team ID, e.g. T0123ABC")
        p.set_defaults(func=_add_person, relation=relation)

    p = sub.add_parser("serve", help="run the backend API (app, console and webhooks) on localhost:8000")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=_serve)

    p = sub.add_parser("worker", help="run every Ava's jobs and Telegram/Slack listeners in this process")
    p.add_argument("--refresh", type=float, default=15.0, help="seconds between checks for changed connections")
    p.add_argument("--no-jobs", action="store_true", help="listeners only")
    p.add_argument("--no-listeners", action="store_true", help="jobs only")
    p.set_defaults(func=_worker)
    sub.add_parser("telegram-poll", help="receive Telegram messages by polling").set_defaults(func=_telegram_poll)
    sub.add_parser("slack-socket", help="receive Slack messages over Socket Mode").set_defaults(func=_slack_socket)

    sub.add_parser("freeze", help="kill switch on").set_defaults(func=_freeze, on=True)
    sub.add_parser("unfreeze", help="kill switch off").set_defaults(func=_freeze, on=False)
    sub.add_parser("spend", help="show Claude API spend against the cap").set_defaults(func=_spend)
    sub.add_parser("push-keys", help="show the web push key, making it once when PUSH_ENABLED is on").set_defaults(
        func=_push_keys
    )

    p = sub.add_parser("unlink", help="unlink a channel identity from its person")
    p.add_argument("--identity", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=_unlink)

    sub.add_parser("hello", help="one tiny real Claude call to test the key").set_defaults(func=_hello)
    sub.add_parser("seed-demo", help="add demo people if none exist").set_defaults(func=_seed_demo)
    p = sub.add_parser("reset-password", help="set a new password for an app account")
    p.add_argument("--email", required=True)
    p.set_defaults(func=_reset_password)
    p = sub.add_parser("reset-db", help="delete all data")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=_reset_db)

    args = parser.parse_args()
    args.func(args)
