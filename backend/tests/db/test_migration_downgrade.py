"""Downgrading migration 0023 must work after an escalation was snoozed (issue #92, BE-22).

Run in a scratch schema, like the row-level security migration check: upgrade to 0023, add rows the
old unique index would reject, downgrade to 0022.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import text

from superaide.platform.db.session import get_engine

BACKEND = Path(__file__).resolve().parents[2]


def _alembic(env: dict[str, str], *args: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr[-2000:]


def test_downgrading_0023_keeps_the_latest_cycle_of_a_snoozed_rung():
    schema = f"mig_down_{uuid.uuid4().hex[:8]}"
    url = get_engine().url.render_as_string(hide_password=False)
    with get_engine().begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = f"{url}{'&' if '?' in url else '?'}options={quote(f'-csearch_path={schema},public')}"
        env = {**os.environ, "DATABASE_URL": scoped, "MIGRATION_DATABASE_URL": ""}
        _alembic(env, "upgrade", "0023")
        escalation, tenant, agent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        with get_engine().begin() as conn:
            conn.execute(text(f"SET LOCAL search_path = {schema}, public"))
            conn.execute(text("SET LOCAL session_replication_role = replica"))  # the parent rows don't matter here
            # One rung (step 1, telegram) sent in cycle 0, then again after two snoozes; app once.
            for cycle, provider in [(0, "telegram"), (1, "telegram"), (2, "telegram"), (0, "app")]:
                conn.execute(
                    text(
                        "INSERT INTO escalation_delivery (id, tenant_id, agent_id, escalation_id, step, cycle,"
                        " provider, token, external_ref, error, status)"
                        " VALUES (:id, :t, :a, :e, 1, :c, :p, :tok, '', '', 'sent')"
                    ),
                    {
                        "id": uuid.uuid4(), "t": tenant, "a": agent, "e": escalation, "c": cycle, "p": provider,
                        "tok": uuid.uuid4().hex[:24],
                    },
                )  # fmt: skip
        _alembic(env, "downgrade", "0022")
        with get_engine().connect() as conn:
            conn.execute(text(f"SET search_path = {schema}, public"))
            rows = conn.execute(text("SELECT provider, step FROM escalation_delivery ORDER BY provider")).all()
        assert [tuple(r) for r in rows] == [("app", 1), ("telegram", 1)]
    finally:
        with get_engine().begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
