"""Test setup: a real Postgres database, a fake model and fake messengers.

Tests never call the Claude API, so they cost nothing. They use the database in
TEST_DATABASE_URL (from the environment or .env) and wipe it on every run, so it
must not be your real database.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

_env_file = Path(__file__).resolve().parents[1] / ".env"
_from_file = dotenv_values(_env_file) if _env_file.exists() else {}
os.environ.setdefault(
    "TEST_DATABASE_URL",
    _from_file.get("TEST_DATABASE_URL") or "postgresql+psycopg://agent:agent@localhost:5432/agent_test",
)
# Load the guard by path: importing the superaide package here would read settings before the
# environment below is prepared.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_superaide_db_safety", Path(__file__).resolve().parents[1] / "src/superaide/platform/db/safety.py"
)
assert _spec and _spec.loader
safety = importlib.util.module_from_spec(_spec)
import sys  # noqa: E402

sys.modules["_superaide_db_safety"] = safety
_spec.loader.exec_module(safety)

# The tests wipe their database, so it must be disposable (named test/eval/scratch/e2e) and must not be
# the live DATABASE_URL, compared after normalising host, driver, port, case and an xdist suffix. An xdist
# worker inherits the controller's DATABASE_URL (already the test database), so only the controller
# compares against the process environment; every process compares against backend/.env.
_process_live = () if os.environ.get("PYTEST_XDIST_WORKER") else (os.environ.get("DATABASE_URL") or "",)
safety.assert_disposable_database(os.environ["TEST_DATABASE_URL"], extra_live=tuple(u for u in _process_live if u))


def _worker_database(url: str, worker: str) -> str:
    """pytest-xdist: give worker gwN its own database `<name>_gwN`, created on demand.

    The suite truncates every table before each test, so workers cannot share one database.
    Databases are created from the admin database of the same server and never dropped (the
    schema fixture rebuilds the tables on every run). Without xdist (or with `-p no:xdist`)
    nothing changes and the plain TEST_DATABASE_URL is used.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    name = f"{parsed.database}_{worker}"
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.exec_driver_sql("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).first()
            if not exists:
                conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        admin.dispose()
    return parsed.set(database=name).render_as_string(hide_password=False)


_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER")  # "gw0", "gw1", ...; unset without xdist
if _xdist_worker:
    os.environ["TEST_DATABASE_URL"] = _worker_database(os.environ["TEST_DATABASE_URL"], _xdist_worker)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.update(
    {
        "AGENT_NAME": "Ava",
        "TELEGRAM_WEBHOOK_SECRET": "test-telegram-secret",
        "SLACK_SIGNING_SECRET": "test-slack-secret",
        "SLACK_TEAM_ID": "T_OURS",
        "ADMIN_TOKEN": "test-admin-token",
        "ENABLE_SIMULATOR": "true",
        "APPROVAL_REQUIRE_EXECUTOR": "false",  # tests opt in (tests/policy/test_approval_honesty.py)
        "MANDATES_ENABLED": "false",  # on by default (#448); pinned off here, tests opt in (tests/mandate)
        "MANDATE_SHADOW_ENABLED": "false",
        "MANDATE_SKIP_ENABLED": "false",
        "TASK_PROTOCOL_ENABLED": "false",  # on by default (#448); pinned off here, tests opt in
        "FAKE_LLM": "true",
        "EMBEDDER": "fake",
        "VOYAGE_API_KEY": "",
        "MAX_SPEND_USD": "10",
        "ANTHROPIC_API_KEY": "",
        "TELEGRAM_BOT_TOKEN": "",
        "SLACK_BOT_TOKEN": "",
    }
)

from collections.abc import Callable, Iterator  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

import superaide.plugins  # noqa: E402, F401  (registers the approval executors and event subscribers)
from superaide.brain.llm import FakeLLM  # noqa: E402
from superaide.brain.turn import Prediction, RequestedAction, TurnOutput  # noqa: E402
from superaide.channels.base import FakeMessenger, InboundMessage  # noqa: E402
from superaide.identity.linking import bootstrap_person  # noqa: E402
from superaide.memory.recall.vectors import ensure_vector_store  # noqa: E402
from superaide.pipeline import Agent  # noqa: E402
from superaide.platform.config import get_settings  # noqa: E402
from superaide.platform.db.models import Base, Person  # noqa: E402
from superaide.platform.db.session import ensure_extensions, get_engine, session_scope  # noqa: E402
from tests import modules, sharding  # noqa: E402

MANAGER_TG = "1001"
MANAGER_SLACK = "U_MGR"
COLLEAGUE_SLACK = "U_PRIYA"
OUR_TEAM = "T_OURS"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    engine = get_engine()
    safety.assert_disposable_database(engine.url)
    ensure_extensions(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS memory_embedding, chunk_embedding"))  # outside the ORM metadata
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ensure_vector_store(engine)
    yield


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    safety.assert_disposable_database(get_engine().url)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {names} CASCADE"))
    yield


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def outbox() -> dict[str, FakeMessenger]:
    return {"telegram": FakeMessenger(), "slack": FakeMessenger(), "web": FakeMessenger()}


class ScriptedModel:
    """Fake model that records the prompt and returns a chosen TurnOutput."""

    def __init__(self) -> None:
        self.systems: list[str] = []
        self.next_action = RequestedAction(kind="none", summary="")
        self.reply = "ok"

    def __call__(self, purpose, system, messages, output_model) -> Any:
        self.systems.append("\n".join(b.text for b in system))
        if output_model is None:  # the owner's tool loop: an action is a propose_action call
            last = messages[-1]["content"] if messages else ""
            answered = isinstance(last, list) and any(b.get("type") == "tool_result" for b in last)
            if self.next_action.kind != "none" and not answered:
                return [
                    {
                        "type": "tool_use",
                        "id": "toolu_scripted",
                        "name": "propose_action",
                        "input": {
                            "kind": self.next_action.kind,
                            "summary": self.next_action.summary,
                            "rationale": "asked",
                            "reaches_a_person": "yes",
                        },
                    }
                ]
            return self.reply
        return TurnOutput(
            reply=self.reply,
            requested_action=self.next_action,
            prediction=Prediction(expectation="they will reply", confidence=0.6),
        )


@pytest.fixture
def model() -> ScriptedModel:
    return ScriptedModel()


@pytest.fixture
def agent(model: ScriptedModel, outbox, settings) -> Agent:
    return Agent(llm=FakeLLM(model), messengers=outbox, settings=settings)


@pytest.fixture
def manager() -> Person:
    with session_scope() as s:
        return bootstrap_person(
            s,
            name="Jordan",
            relation="manager",
            identities=[("telegram", "", MANAGER_TG), ("slack", OUR_TEAM, MANAGER_SLACK)],
        )


@pytest.fixture
def colleague() -> Person:
    with session_scope() as s:
        return bootstrap_person(
            s, name="Priya Tan", relation="colleague", identities=[("slack", OUR_TEAM, COLLEAGUE_SLACK)]
        )


def tg(
    sender_id: str, text: str, *, chat_kind: str = "dm", chat_id: str | None = None, display: str = ""
) -> InboundMessage:
    return InboundMessage(
        provider="telegram",
        tenant_scope="",
        sender_id=sender_id,
        chat_id=chat_id or sender_id,
        chat_kind=chat_kind,
        text=text,
        verified=True,
        sender_display=display,
    )


def sl(
    user: str,
    text: str,
    *,
    team: str = OUR_TEAM,
    chat_kind: str = "dm",
    channel: str | None = None,
    display: str = "",
    external: bool = False,
) -> InboundMessage:
    return InboundMessage(
        provider="slack",
        tenant_scope=team,
        sender_id=user,
        chat_id=channel or f"D_{user}",
        chat_kind=chat_kind,
        text=text,
        verified=True,
        sender_display=display,
        external_sender=external,
        raw={"team_id": OUR_TEAM},
    )


def codes_sent(messenger: FakeMessenger) -> list[str]:
    import re

    return [m.group(1) for sent in messenger.sent for m in [re.search(r"\b(\d{6})\b", sent["text"])] if m]


Factory = Callable[..., InboundMessage]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shard", default=None, help="run one slice of the suite, e.g. --shard 1/2 (CI splits the backend tests)"
    )


def pytest_configure(config: pytest.Config) -> None:
    modules.register(config)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    modules.tag(config, items)  # `pytest -m mod_memory` runs one module (ci/modules.toml)
    spec = config.getoption("--shard")
    if not spec:
        return
    index, total = sharding.parse(spec)
    owner = sharding.assign([i.nodeid for i in items], total)
    mine = [i for i in items if owner[sharding.module_of(i.nodeid)] == index]
    config.hook.pytest_deselected(items=[i for i in items if i not in mine])
    items[:] = mine
