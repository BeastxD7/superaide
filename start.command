#!/bin/bash
# Try agi-agent on your Mac with one command: database, agent and dashboard.
#
#   bash ~/Desktop/agi-agent/start.command           start (Ctrl+C to stop the agent)
#   bash ~/Desktop/agi-agent/start.command --reset   wipe the local data first
#
# Uses Docker for Postgres if Docker is running; otherwise an embedded Postgres
# (the pgserver package) stored in .local-db/. Runs with the offline fake model
# unless you put ANTHROPIC_API_KEY in .env and set FAKE_LLM=false.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
bold() { printf "\n\033[1m%s\033[0m\n" "$*"; }

# 1. uv (Python package manager)
if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    bold "Installing uv into ~/.local/bin (one time)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

# 2. .env with an admin token
new_token() { openssl rand -hex 24 2>/dev/null || date +%s%N | shasum | cut -c1-48; }
if [ ! -f .env ]; then
  bold "Creating .env (offline model, simulator on, no API spend)…"
  cp .env.example .env
fi
if grep -q '^ADMIN_TOKEN=$' .env; then
  TOKEN="$(new_token)"
  tmp="$(mktemp)"
  sed -e "s|^ADMIN_TOKEN=\$|ADMIN_TOKEN=$TOKEN|" -e "s|^SLACK_TEAM_ID=\$|SLACK_TEAM_ID=T_DEMO|" .env > "$tmp" && mv "$tmp" .env
fi
TOKEN="$(grep '^ADMIN_TOKEN=' .env | head -1 | cut -d= -f2-)"

# 3. Database
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  bold "Starting Postgres in Docker…"
  docker compose up -d --wait
  export DATABASE_URL="postgresql+psycopg://agent:agent@localhost:5432/agent"
else
  bold "Starting a local Postgres (no Docker needed; first run downloads it)…"
  mkdir -p "$ROOT/.local-db"
  URI="$(uv run --quiet --no-project --python 3.12 --with pgserver python scripts/local_db.py "$ROOT/.local-db/pgdata" 2>/dev/null | tail -1)"
  case "$URI" in
    postgresql://*) export DATABASE_URL="postgresql+psycopg://${URI#postgresql://}" ;;
    *) echo "Could not start the local database. Install Docker Desktop and run this again."; exit 1 ;;
  esac
fi

# 4. Python dependencies and tables
bold "Installing Python dependencies (first run takes a minute)…"
uv sync --quiet
if [ "${1:-}" = "--reset" ]; then
  uv run --quiet agi-agent reset-db --yes
fi
uv run --quiet alembic upgrade head
uv run --quiet agi-agent seed-demo

# 5. Dashboard: prebuilt in web/dist; rebuild only if missing and Node exists
if [ ! -f web/dist/index.html ] && command -v npm >/dev/null 2>&1; then
  bold "Building the dashboard…"
  (cd web && npm install --no-audit --no-fund && npm run build)
fi

# 6. Run
PORT=8000
if command -v lsof >/dev/null 2>&1; then
  while lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do PORT=$((PORT + 1)); done
fi
URL="http://127.0.0.1:$PORT/#token=$TOKEN"
bold "Agent running. Dashboard: http://127.0.0.1:$PORT  (Ctrl+C to stop)"
echo "Admin token: $TOKEN"
( sleep 3; open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true ) &
exec uv run agi-agent serve --port "$PORT"
