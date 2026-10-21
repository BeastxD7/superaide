"""Command line: `uv run agi-agent <command>`."""

from __future__ import annotations

import argparse
import logging
import sys
import uuid

from agi_agent.config import get_settings


def _init_db(_: argparse.Namespace) -> None:
    """Create tables directly (quick start). For real deployments use `alembic upgrade head`."""
    from agi_agent.db.models import Base
    from agi_agent.db.session import get_engine

    Base.metadata.create_all(get_engine())
    print("Tables created.")


def _add_person(args: argparse.Namespace) -> None:
    from agi_agent.db.session import session_scope
    from agi_agent.identity.linking import bootstrap_person

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
    uvicorn.run("agi_agent.main:app", host="127.0.0.1", port=args.port, reload=args.reload)


def _telegram_poll(_: argparse.Namespace) -> None:
    from agi_agent.channels.telegram import poll_forever
    from agi_agent.factory import build_agent

    settings = get_settings()
    if not settings.telegram_bot_token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env")
    poll_forever(settings.telegram_bot_token, build_agent(settings).handle)


def _slack_socket(_: argparse.Namespace) -> None:
    from agi_agent.channels.slack import run_socket_mode
    from agi_agent.factory import build_agent

    settings = get_settings()
    if not (settings.slack_bot_token and settings.slack_app_token):
        sys.exit("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env")
    run_socket_mode(
        settings.slack_bot_token, settings.slack_app_token, settings.slack_team_id, build_agent(settings).handle
    )


def _freeze(args: argparse.Namespace) -> None:
    from agi_agent.db.session import session_scope
    from agi_agent.policy.killswitch import set_frozen

    with session_scope() as s:
        set_frozen(s, args.on, actor="cli")
    print("Agent frozen: no model calls, no replies." if args.on else "Agent running.")


def _spend(_: argparse.Namespace) -> None:
    from agi_agent.brain.budget import BudgetGuard

    summary = BudgetGuard(get_settings().max_spend_usd).summary()
    print(
        f"Spent US${summary.settled_usd:.4f} of US${summary.cap_usd:.2f} "
        f"({summary.calls} calls, US${summary.reserved_usd:.4f} reserved, US${summary.remaining_usd:.4f} left)"
    )


def _unlink(args: argparse.Namespace) -> None:
    from agi_agent.db.models import ChannelIdentity
    from agi_agent.db.session import session_scope
    from agi_agent.identity.linking import unlink

    with session_scope() as s:
        identity = s.get(ChannelIdentity, uuid.UUID(args.identity))
        if identity is None or identity.person_id is None:
            sys.exit("No linked identity with that id")
        unlink(s, identity, actor="cli", reason=args.reason)
    print("Unlinked.")


def _hello(_: argparse.Namespace) -> None:
    """One tiny real Claude call (Haiku, ~40 output tokens) to check the key and the ledger."""
    from agi_agent.brain.budget import BudgetGuard
    from agi_agent.brain.llm import ClaudeLLM, SystemBlock

    settings = get_settings()
    llm = ClaudeLLM(BudgetGuard(settings.max_spend_usd), api_key=settings.anthropic_api_key)
    result = llm.complete(
        purpose="smoke_test",
        model=settings.model_triage,
        system=[SystemBlock("Reply in one short sentence.")],
        messages=[{"role": "user", "content": "Say hello to the agi-agent project."}],
        max_tokens=40,
    )
    print(result.text)
    print(f"Cost: US${result.cost_usd:.6f}")
    _spend(_)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="agi-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="create tables").set_defaults(func=_init_db)

    for name, relation in (("add-manager", "manager"), ("add-colleague", "colleague")):
        p = sub.add_parser(name, help=f"register a {relation} and their first channels")
        p.add_argument("--name", required=True)
        p.add_argument("--telegram-id", help="numeric Telegram user ID")
        p.add_argument("--slack-user", help="Slack user ID, e.g. U0123ABC")
        p.add_argument("--slack-team", help="Slack team ID, e.g. T0123ABC")
        p.set_defaults(func=_add_person, relation=relation)

    p = sub.add_parser("serve", help="run the API (webhooks + dashboard API) on localhost")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=_serve)

    sub.add_parser("telegram-poll", help="receive Telegram messages by polling").set_defaults(func=_telegram_poll)
    sub.add_parser("slack-socket", help="receive Slack messages over Socket Mode").set_defaults(func=_slack_socket)

    sub.add_parser("freeze", help="kill switch on").set_defaults(func=_freeze, on=True)
    sub.add_parser("unfreeze", help="kill switch off").set_defaults(func=_freeze, on=False)
    sub.add_parser("spend", help="show Claude API spend against the cap").set_defaults(func=_spend)

    p = sub.add_parser("unlink", help="unlink a channel identity from its person")
    p.add_argument("--identity", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=_unlink)

    sub.add_parser("hello", help="one tiny real Claude call to test the key").set_defaults(func=_hello)

    args = parser.parse_args()
    args.func(args)
