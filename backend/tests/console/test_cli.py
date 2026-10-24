"""The operator's terminal: `uv run superaide <command>`, driven through main().

Long-running commands (serve, telegram-poll, slack-socket) and the real Claude
call in `hello` are replaced with recorders, so nothing here touches the network.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from typing import Any

import pytest
import uvicorn
from sqlalchemy import func, inspect, select, text

from superaide import cli
from superaide.auth.service import AuthError, Client, login, signup, user_for_token
from superaide.brain.llm import LLMResult
from superaide.brain.pricing import UsageCounts
from superaide.config import Settings, get_settings
from superaide.db.models import AuditLog, Base, ChannelIdentity, IdentityLinkEvent, Person, SpendEntry
from superaide.db.session import get_engine, session_scope
from superaide.identity.trust import Trust
from superaide.pipeline import Agent
from superaide.policy.killswitch import is_frozen
from tests.conftest import MANAGER_TG, OUR_TEAM

OLD_PASSWORD = "correct horse battery"
NEW_PASSWORD = "purple elephant marmalade"


def run(monkeypatch, capsys, *argv: str) -> str:
    monkeypatch.setattr(sys, "argv", ["superaide", *argv])
    cli.main()
    return capsys.readouterr().out


def run_and_exit(monkeypatch, *argv: str) -> Any:
    """Run a command that should stop with sys.exit; returns the exit code (the message for sys.exit("...")."""
    monkeypatch.setattr(sys, "argv", ["superaide", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def use_settings(monkeypatch, **overrides: Any) -> Settings:
    settings = get_settings().model_copy(update=overrides)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def identity(provider: str, scope: str, key: str) -> ChannelIdentity | None:
    with session_scope() as s:
        return s.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.provider == provider,
                ChannelIdentity.tenant_scope == scope,
                ChannelIdentity.stable_key == key,
            )
        )


def count(model: type) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


# --- init-db and reset-db -------------------------------------------------------------


def test_init_db_creates_the_tables_and_is_safe_to_run_twice(monkeypatch, capsys):
    assert run(monkeypatch, capsys, "init-db").strip() == "Tables created."
    assert run(monkeypatch, capsys, "init-db").strip() == "Tables created."
    assert set(Base.metadata.tables) <= set(inspect(get_engine()).get_table_names())


def test_reset_db_refuses_without_yes_and_keeps_the_data(monkeypatch, manager):
    message = run_and_exit(monkeypatch, "reset-db")
    assert "--yes" in message
    assert count(Person) == 1


def test_reset_db_with_yes_drops_every_table_and_the_alembic_version(monkeypatch, capsys, manager):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)"))
    try:
        assert run(monkeypatch, capsys, "reset-db", "--yes").strip() == "Database wiped."
        left = set(inspect(engine).get_table_names())
        assert not left & set(Base.metadata.tables)
        assert "alembic_version" not in left
    finally:
        # The other tests share this schema; put it back.
        Base.metadata.create_all(engine)
    assert run(monkeypatch, capsys, "init-db").strip() == "Tables created."
    assert count(Person) == 0


# --- add-manager / add-colleague ------------------------------------------------------


def test_add_manager_links_telegram_and_slack_to_a_new_manager(monkeypatch, capsys):
    out = run(
        monkeypatch,
        capsys,
        "add-manager",
        "--name",
        "Jordan",
        "--telegram-id",
        "4242",
        "--slack-user",
        "U_BOSS",
        "--slack-team",
        "T_OTHER",
    )
    assert "Added Jordan (manager) with 2 channel(s)." in out

    with session_scope() as s:
        person = s.scalar(select(Person))
        assert person is not None
        assert (person.display_name, person.relation) == ("Jordan", "manager")
        assert f"id={person.id}" in out
        person_id = person.id
        events = s.scalars(select(IdentityLinkEvent).where(IdentityLinkEvent.person_id == person_id)).all()
        assert {(e.action, e.method) for e in events} == {("link", "operator_bootstrap")}
        assert len(events) == 2

    telegram = identity("telegram", "", "4242")
    slack = identity("slack", "T_OTHER", "U_BOSS")
    for linked in (telegram, slack):
        assert linked is not None
        assert linked.person_id == person_id
        assert linked.trust_level == Trust.LINKED
        assert linked.verification["method"] == "operator_bootstrap"


def test_add_colleague_with_slack_only_uses_the_team_from_settings(monkeypatch, capsys):
    out = run(monkeypatch, capsys, "add-colleague", "--name", "Priya Tan", "--slack-user", "U_PRIYA2")
    assert "Added Priya Tan (colleague) with 1 channel(s)." in out

    linked = identity("slack", OUR_TEAM, "U_PRIYA2")
    assert linked is not None
    with session_scope() as s:
        person = s.get(Person, linked.person_id)
        assert person is not None and person.relation == "colleague"
    assert count(ChannelIdentity) == 1


def test_add_person_takes_over_an_existing_unlinked_identity_instead_of_duplicating_it(monkeypatch, capsys):
    with session_scope() as s:
        s.add(ChannelIdentity(provider="telegram", tenant_scope="", stable_key="777", aux_handles={}))
    run(monkeypatch, capsys, "add-colleague", "--name", "Dev", "--telegram-id", "777")
    assert count(ChannelIdentity) == 1
    linked = identity("telegram", "", "777")
    assert linked is not None and linked.person_id is not None


def test_add_person_without_a_slack_team_anywhere_stops_and_adds_no_one(monkeypatch):
    use_settings(monkeypatch, slack_team_id="")
    message = run_and_exit(monkeypatch, "add-manager", "--name", "Jordan", "--slack-user", "U_BOSS")
    assert message == "Give --slack-team or set SLACK_TEAM_ID in .env"
    assert count(Person) == 0


def test_add_person_needs_at_least_one_channel(monkeypatch):
    message = run_and_exit(monkeypatch, "add-colleague", "--name", "Nobody")
    assert message == "Give at least one of --telegram-id or --slack-user"
    assert count(Person) == 0


def test_add_person_needs_a_name(monkeypatch, capsys):
    assert run_and_exit(monkeypatch, "add-manager", "--telegram-id", "1") == 2
    assert "--name" in capsys.readouterr().err


# --- unlink ---------------------------------------------------------------------------


def test_unlink_detaches_the_identity_and_records_why(monkeypatch, capsys, manager):
    linked = identity("telegram", "", MANAGER_TG)
    assert linked is not None and linked.person_id == manager.id

    out = run(monkeypatch, capsys, "unlink", "--identity", str(linked.id), "--reason", "lost phone")
    assert out.strip() == "Unlinked."

    after = identity("telegram", "", MANAGER_TG)
    assert after is not None
    assert after.person_id is None
    assert after.trust_level == Trust.AUTHENTICATED
    with session_scope() as s:
        event = s.scalar(select(IdentityLinkEvent).where(IdentityLinkEvent.action == "unlink"))
        assert event is not None
        assert event.person_id == manager.id
        assert event.actor == "cli"
        assert event.evidence == {"reason": "lost phone"}
    # The other channel stays linked.
    slack = identity("slack", OUR_TEAM, "U_MGR")
    assert slack is not None and slack.person_id == manager.id


def test_unlink_an_unknown_or_already_unlinked_identity_fails_clearly(monkeypatch, capsys, manager):
    unknown = "00000000-0000-4000-8000-00000000abcd"
    assert run_and_exit(monkeypatch, "unlink", "--identity", unknown) == "No linked identity with that id"

    linked = identity("telegram", "", MANAGER_TG)
    assert linked is not None
    run(monkeypatch, capsys, "unlink", "--identity", str(linked.id))
    assert run_and_exit(monkeypatch, "unlink", "--identity", str(linked.id)) == "No linked identity with that id"


def test_unlink_with_a_malformed_id_says_so_instead_of_crashing(monkeypatch):
    message = run_and_exit(monkeypatch, "unlink", "--identity", "not-a-uuid")
    assert "id" in message


# --- freeze / unfreeze / spend ---------------------------------------------------------


def frozen() -> bool:
    with session_scope() as s:
        return is_frozen(s)


def test_freeze_and_unfreeze_flip_the_kill_switch_and_are_audited(monkeypatch, capsys):
    assert not frozen()

    assert run(monkeypatch, capsys, "freeze").strip() == "Agent frozen: no model calls, no replies."
    assert frozen()
    assert run(monkeypatch, capsys, "freeze").strip() == "Agent frozen: no model calls, no replies."
    assert frozen()

    assert run(monkeypatch, capsys, "unfreeze").strip() == "Agent running."
    assert not frozen()

    with session_scope() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "kill_switch")).all()
        assert len(rows) == 3
        assert {r.actor for r in rows} == {"cli"}


def test_spend_on_an_empty_ledger_shows_the_whole_cap_left(monkeypatch, capsys):
    out = run(monkeypatch, capsys, "spend").strip()
    assert out == "Spent US$0.0000 of US$10.00 (0 calls, US$0.0000 reserved, US$10.0000 left)"


def test_spend_counts_settled_calls_and_open_reservations(monkeypatch, capsys):
    with session_scope() as s:
        s.add(
            SpendEntry(purpose="chat", model="m", status="settled", reserved_usd=Decimal("1"), cost_usd=Decimal("1.25"))
        )
        s.add(SpendEntry(purpose="chat", model="m", status="settled", cost_usd=Decimal("0.5")))
        s.add(SpendEntry(purpose="chat", model="m", status="reserved", reserved_usd=Decimal("2")))
        s.add(SpendEntry(purpose="chat", model="m", status="failed", reserved_usd=Decimal("3")))
    out = run(monkeypatch, capsys, "spend").strip()
    assert out == "Spent US$1.7500 of US$10.00 (2 calls, US$2.0000 reserved, US$6.2500 left)"


# --- seed-demo ------------------------------------------------------------------------


def test_seed_demo_adds_two_people_once_and_then_nothing(monkeypatch, capsys):
    out = run(monkeypatch, capsys, "seed-demo")
    assert "Added demo people: Jordan (manager) and Priya Tan (colleague)." in out
    with session_scope() as s:
        people = {p.display_name: p.relation for p in s.scalars(select(Person))}
    assert people == {"Jordan": "manager", "Priya Tan": "colleague"}
    assert count(ChannelIdentity) == 3
    manager_slack = identity("slack", OUR_TEAM, "U_DEMO_JORDAN")
    assert manager_slack is not None and manager_slack.trust_level == Trust.LINKED
    assert identity("telegram", "", "demo-1001") is not None

    again = run(monkeypatch, capsys, "seed-demo")
    assert "People already registered; demo data not added." in again
    assert count(Person) == 2
    assert count(ChannelIdentity) == 3
    assert count(IdentityLinkEvent) == 3


def test_seed_demo_leaves_real_people_alone(monkeypatch, capsys, manager):
    assert "demo data not added" in run(monkeypatch, capsys, "seed-demo")
    assert count(Person) == 1
    assert identity("telegram", "", "demo-1001") is None


def test_seed_demo_uses_a_demo_team_when_none_is_configured(monkeypatch, capsys):
    use_settings(monkeypatch, slack_team_id="")
    run(monkeypatch, capsys, "seed-demo")
    assert identity("slack", "T_DEMO", "U_DEMO_PRIYA") is not None


# --- reset-password -------------------------------------------------------------------


def make_account(email: str = "owner@example.com") -> str:
    with session_scope() as s:
        _, token = signup(s, email=email, password=OLD_PASSWORD, name="Jordan Whitfield", client=Client())
    return token


def type_passwords(monkeypatch, *answers: str) -> None:
    import getpass

    it = iter(answers)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(it))


def test_reset_password_sets_the_new_one_and_signs_out_everywhere(monkeypatch, capsys):
    old_token = make_account()
    with session_scope() as s:
        assert user_for_token(s, old_token) is not None

    type_passwords(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)
    out = run(monkeypatch, capsys, "reset-password", "--email", "Owner@Example.com")
    assert "Password changed for Jordan Whitfield." in out

    with session_scope() as s:
        assert user_for_token(s, old_token) is None
        user, new_token = login(s, email="owner@example.com", password=NEW_PASSWORD, client=Client())
        assert user.display_name == "Jordan Whitfield"
    with session_scope() as s:
        assert user_for_token(s, new_token) is not None
    with pytest.raises(AuthError), session_scope() as s:
        login(s, email="owner@example.com", password=OLD_PASSWORD, client=Client())


def test_reset_password_for_an_unknown_email_fails_clearly(monkeypatch):
    type_passwords(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)
    assert run_and_exit(monkeypatch, "reset-password", "--email", "nobody@example.com") == (
        "No account for nobody@example.com."
    )


def test_reset_password_stops_when_the_two_entries_differ(monkeypatch):
    old_token = make_account()
    type_passwords(monkeypatch, NEW_PASSWORD, NEW_PASSWORD + "x")
    message = run_and_exit(monkeypatch, "reset-password", "--email", "owner@example.com")
    assert message == "The two passwords don't match. Nothing changed."
    with session_scope() as s:
        assert user_for_token(s, old_token) is not None
        login(s, email="owner@example.com", password=OLD_PASSWORD, client=Client())


def test_reset_password_rejects_a_weak_password_and_keeps_the_old_one(monkeypatch):
    old_token = make_account()
    type_passwords(monkeypatch, "short", "short")
    assert run_and_exit(monkeypatch, "reset-password", "--email", "owner@example.com") == (
        "Use at least 10 characters."
    )
    with session_scope() as s:
        assert user_for_token(s, old_token) is not None


# --- long-running commands ------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


def test_serve_binds_localhost_on_the_default_port(monkeypatch, capsys):
    spy = Recorder()
    monkeypatch.setattr(uvicorn, "run", spy)
    run(monkeypatch, capsys, "serve")
    assert spy.calls == [(("superaide.main:app",), {"host": "127.0.0.1", "port": 8000, "reload": False})]


def test_serve_passes_port_and_reload(monkeypatch, capsys):
    spy = Recorder()
    monkeypatch.setattr(uvicorn, "run", spy)
    run(monkeypatch, capsys, "serve", "--port", "8123", "--reload")
    assert spy.calls == [(("superaide.main:app",), {"host": "127.0.0.1", "port": 8123, "reload": True})]


def test_serve_rejects_a_port_that_is_not_a_number(monkeypatch):
    monkeypatch.setattr(uvicorn, "run", Recorder())
    assert run_and_exit(monkeypatch, "serve", "--port", "eighty") == 2


def test_telegram_poll_needs_a_bot_token(monkeypatch):
    import superaide.channels.telegram as telegram

    spy = Recorder()
    monkeypatch.setattr(telegram, "poll_forever", spy)
    assert run_and_exit(monkeypatch, "telegram-poll") == "Set TELEGRAM_BOT_TOKEN in .env"
    assert spy.calls == []


def test_telegram_poll_feeds_updates_to_an_agent_built_from_settings(monkeypatch, capsys):
    import superaide.channels.telegram as telegram

    settings = use_settings(monkeypatch, telegram_bot_token="123:fake")
    spy = Recorder()
    monkeypatch.setattr(telegram, "poll_forever", spy)
    run(monkeypatch, capsys, "telegram-poll")

    [(args, kwargs)] = spy.calls
    assert kwargs == {}
    token, handle = args
    assert token == "123:fake"
    assert isinstance(handle.__self__, Agent)
    assert handle.__func__ is Agent.handle
    assert handle.__self__.settings is settings
    assert isinstance(handle.__self__.messengers["telegram"], telegram.TelegramMessenger)


@pytest.mark.parametrize(
    "tokens",
    [{}, {"slack_bot_token": "xoxb-fake"}, {"slack_app_token": "xapp-fake"}],
    ids=["no tokens", "bot token only", "app token only"],
)
def test_slack_socket_needs_both_tokens(monkeypatch, tokens):
    import superaide.channels.slack as slack

    use_settings(monkeypatch, **{"slack_bot_token": "", "slack_app_token": "", **tokens})
    spy = Recorder()
    monkeypatch.setattr(slack, "run_socket_mode", spy)
    assert run_and_exit(monkeypatch, "slack-socket") == "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env"
    assert spy.calls == []


def test_slack_socket_passes_tokens_team_and_the_agent_handler(monkeypatch, capsys):
    import superaide.channels.slack as slack

    settings = use_settings(monkeypatch, slack_bot_token="xoxb-fake", slack_app_token="xapp-fake")
    spy = Recorder()
    monkeypatch.setattr(slack, "run_socket_mode", spy)
    run(monkeypatch, capsys, "slack-socket")

    [(args, kwargs)] = spy.calls
    assert kwargs == {}
    bot, app, team, handle = args
    assert (bot, app, team) == ("xoxb-fake", "xapp-fake", OUR_TEAM)
    assert isinstance(handle.__self__, Agent)
    assert handle.__self__.settings is settings
    assert isinstance(handle.__self__.messengers["slack"], slack.SlackMessenger)


# --- hello ----------------------------------------------------------------------------


def test_hello_makes_one_small_cheap_call_and_reports_cost_and_spend(monkeypatch, capsys):
    import superaide.brain.llm as llm

    made: list[Any] = []

    class StubClaude:
        def __init__(self, guard: Any, api_key: str = "", client: Any = None) -> None:
            self.guard, self.api_key = guard, api_key
            self.calls: list[dict[str, Any]] = []
            made.append(self)

        def complete(self, **kwargs: Any) -> LLMResult:
            self.calls.append(kwargs)
            return LLMResult(
                text="Hello, superaide!",
                parsed=None,
                model=kwargs["model"],
                usage=UsageCounts(),
                cost_usd=Decimal("0.000123"),
            )

    settings = use_settings(monkeypatch, anthropic_api_key="sk-test-not-real")
    monkeypatch.setattr(llm, "ClaudeLLM", StubClaude)
    out = run(monkeypatch, capsys, "hello").splitlines()

    [claude] = made
    assert claude.api_key == "sk-test-not-real"
    assert claude.guard.cap == Decimal("10")
    [call] = claude.calls
    assert call["purpose"] == "smoke_test"
    assert call["model"] == settings.model_triage
    assert call["effort"] == "low"
    assert call["max_tokens"] <= 1000
    assert [m["role"] for m in call["messages"]] == ["user"]
    assert out[0] == "Hello, superaide!"
    assert out[1] == "Cost: US$0.000123"
    assert out[2].startswith("Spent US$0.0000 of US$10.00")


def test_hello_without_an_api_key_refuses_before_any_call(monkeypatch):
    use_settings(monkeypatch, anthropic_api_key="")
    monkeypatch.setattr(sys, "argv", ["superaide", "hello"])
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        cli.main()
    assert count(SpendEntry) == 0


def test_a_command_is_required(monkeypatch):
    assert run_and_exit(monkeypatch) == 2


# --- reset-db refuses the live database --------------------------------------------------


def _live_settings(monkeypatch, tmp_path, name: str = "agent") -> None:
    from superaide.db import safety

    env = tmp_path / ".env"
    env.write_text(f"DATABASE_URL=postgresql+psycopg://agent:agent@localhost:5432/{name}\n")
    monkeypatch.setattr(safety, "ENV_FILE", env)
    use_settings(
        monkeypatch, database_url=f"postgresql+psycopg://agent:agent@127.0.0.1:5432/{name}", migration_database_url=""
    )
    monkeypatch.setattr("superaide.db.session.get_engine", lambda *a, **k: pytest.fail("opened the live database"))


def test_reset_db_refuses_the_live_database_even_with_yes(monkeypatch, tmp_path):
    _live_settings(monkeypatch, tmp_path)
    message = run_and_exit(monkeypatch, "reset-db", "--yes")
    assert "live database" in message and "--i-know-this-is-live --confirm-db-name agent" in message


def test_reset_db_on_the_live_database_needs_the_flag_and_the_name_typed_back(monkeypatch, tmp_path):
    _live_settings(monkeypatch, tmp_path)
    assert "live" in run_and_exit(monkeypatch, "reset-db", "--yes", "--i-know-this-is-live")
    assert "live" in run_and_exit(monkeypatch, "reset-db", "--yes", "--confirm-db-name", "agent")
    assert "live" in run_and_exit(
        monkeypatch, "reset-db", "--yes", "--i-know-this-is-live", "--confirm-db-name", "agen"
    )
