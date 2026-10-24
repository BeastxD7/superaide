"""The database role the app connects as (ADR 0009 T1).

Row-Level Security does nothing for superusers or BYPASSRLS roles, so the API and
workers must not connect as the schema owner. `ensure_app_role` creates (or updates)
a plain login role that may read and write rows but not change the schema, turn
policies off, or bypass them. Run it as the owner after migrations; it is idempotent:

    superaide db-app-role            # role "superaide_app", password from APP_DB_PASSWORD

Grants on future tables are set with ALTER DEFAULT PRIVILEGES, so tables created by
later migrations (run as the same owner) are usable at once.
"""

from __future__ import annotations

import re

from sqlalchemy import Connection, text

DEFAULT_ROLE = "superaide_app"
_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class RoleError(ValueError):
    pass


def ensure_app_role(conn: Connection, *, name: str = DEFAULT_ROLE, password: str) -> None:
    if not _NAME.match(name):
        raise RoleError("role names are lower-case letters, digits and _")
    if len(password) < 16:
        raise RoleError("use a password of at least 16 characters (openssl rand -hex 24)")
    exists = conn.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname = :n"), {"n": name})
    verb = "ALTER" if exists else "CREATE"
    # Identifiers can't be bound parameters: the name is checked above; the password is a quoted literal.
    quoted = "'" + password.replace("'", "''") + "'"
    conn.execute(
        text(
            f"{verb} ROLE {name} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION "
            f"INHERIT PASSWORD {quoted}"
        )
    )
    db = conn.scalar(text("SELECT current_database()"))
    conn.execute(text(f'GRANT CONNECT, TEMPORARY ON DATABASE "{db}" TO {name}'))
    conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {name}"))
    conn.execute(text(f"REVOKE CREATE ON SCHEMA public FROM {name}"))
    conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {name}"))
    conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {name}"))
    conn.execute(
        text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {name}")
    )
    conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {name}"))
    # The worker claims due jobs across instances only through this one function (jobs/claim.py).
    if conn.scalar(text("SELECT to_regprocedure('claim_due_jobs(timestamptz, integer, integer, interval)')")):
        conn.execute(
            text(f"GRANT EXECUTE ON FUNCTION claim_due_jobs(timestamptz, integer, integer, interval) TO {name}")
        )
    # Migrations are the owner's job; the app may not touch their bookkeeping.
    if conn.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL")):
        conn.execute(text(f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {name}"))
