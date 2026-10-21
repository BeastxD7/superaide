#!/usr/bin/env bash
# Start the backend API for one E2E slot: create its database if needed, apply
# migrations, then serve. Playwright runs this (see playwright.config.ts).
set -euo pipefail
cd "$(dirname "$0")/../../backend"

uv run --quiet python - "$E2E_PG_ADMIN_URL" "$E2E_DB_NAME" <<'PY'
import sys

import psycopg

admin_url, name = sys.argv[1], sys.argv[2]
if not name.startswith("superaide_e2e_"):
    sys.exit(f"refusing to use database {name!r}: E2E databases are named superaide_e2e_<slot>")
with psycopg.connect(admin_url, autocommit=True) as conn:
    if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
        conn.execute(f'CREATE DATABASE "{name}"')
PY

uv run --quiet alembic upgrade head
exec uv run --quiet superaide serve --port "$E2E_API_PORT"
