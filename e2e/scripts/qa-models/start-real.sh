#!/usr/bin/env bash
# Start slot 4's API with FAKE_LLM=false, its Claude calls going only to a local fake on :8404.
# Used by e2e/tests/qa-models.spec.ts for the credits, no-limit and zero-cap passes (see that spec's header).
# Extra env (PLATFORM_*, BUDGET_*) comes from the caller. Nothing leaves the machine.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${QA_OUT_DIR:-$HERE/out}"
mkdir -p "$OUT"
: > "$OUT/fake-claude.log"
(cd /tmp && python3 -I "$HERE/fake_claude.py" 8404 "$OUT/fake-claude.log") > "$OUT/fake-claude.out" 2>&1 &
export DATABASE_URL=postgresql+psycopg://agent:agent@localhost:5432/superaide_e2e_4
export FRONTEND_ORIGIN=http://localhost:3104 FAKE_LLM=false ANTHROPIC_API_KEY= TELEGRAM_BOT_TOKEN= SLACK_BOT_TOKEN= SLACK_APP_TOKEN=
export ADMIN_TOKEN=e2e-admin-token ENABLE_SIMULATOR=true RUN_CHANNELS_IN_API=false SIGNUPS_PER_IP_PER_HOUR=100000 LOGIN_FAILURES_PER_IP=100000
export SECRETS_MASTER_KEY=$(node -e 'console.log(Buffer.alloc(32,5).toString("base64"))')
export ANTHROPIC_BASE_URL=http://127.0.0.1:8404 NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
export PLATFORM_AI_ENABLED=true PLATFORM_ANTHROPIC_API_KEY=sk-ant-fake-platform-key-000
export E2E_PG_ADMIN_URL=postgresql://agent:agent@localhost:5432/postgres E2E_DB_NAME=superaide_e2e_4 E2E_API_PORT=8104
exec bash "$HERE/../start-api.sh"
