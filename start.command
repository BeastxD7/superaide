#!/bin/bash
# Run agi-agent on your Mac with one command.
#
#   bash ~/Desktop/agi-agent/start.command               start (Ctrl+C stops everything)
#   bash ~/Desktop/agi-agent/start.command --reset       wipe the data first
#   bash ~/Desktop/agi-agent/start.command --no-docker   embedded Postgres instead of Docker
#
# Three parts, the usual way:
#   database  Postgres 17 in Docker (or an embedded Postgres without Docker)
#   backend   FastAPI, a JSON API only, on http://127.0.0.1:8000  (docs at /docs)
#   frontend  React + Vite on http://localhost:3000, calling the API with Axios
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
API_PORT=8000
WEB_PORT=3000
bold() { printf "\n\033[1m%s\033[0m\n" "$*"; }

RESET=0
USE_DOCKER=1
for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    --no-docker) USE_DOCKER=0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

# 0. Stop a copy of the agent that is already running (an older version, say).
OURS="agi-agent (serve|telegram-poll|slack-socket)|$ROOT/web/node_modules/.bin/vite|http.server $WEB_PORT"
if pgrep -f "$OURS" >/dev/null 2>&1; then
  bold "Stopping the copy of the agent that was already running…"
  pkill -f "$OURS" 2>/dev/null || true
  sleep 2
fi
if command -v lsof >/dev/null 2>&1; then
  for port in "$API_PORT" "$WEB_PORT"; do
    if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      bold "Port $port is in use by another program."
      lsof -iTCP:"$port" -sTCP:LISTEN | tail -n +2
      echo "Close that program (or stop it with: kill <PID>) and run this again."
      exit 1
    fi
  done
fi

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
  bold "Creating .env (offline model, no API spend)…"
  cp .env.example .env
fi
if grep -q '^ADMIN_TOKEN=$' .env; then
  TOKEN="$(new_token)"
  tmp="$(mktemp)"
  sed -e "s|^ADMIN_TOKEN=\$|ADMIN_TOKEN=$TOKEN|" -e "s|^SLACK_TEAM_ID=\$|SLACK_TEAM_ID=T_DEMO|" .env > "$tmp" && mv "$tmp" .env
fi
TOKEN="$(grep '^ADMIN_TOKEN=' .env | head -1 | cut -d= -f2-)"
export FRONTEND_ORIGIN="http://localhost:$WEB_PORT"

# 3. Database: Postgres in Docker. Without Docker Desktop, an embedded Postgres.
docker_usable() {
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1 && return 0
  [ -d /Applications/Docker.app ] || [ -d "$HOME/Applications/Docker.app" ]  # can be started
}
if [ "$USE_DOCKER" = 1 ] && ! docker_usable; then
  bold "Docker Desktop isn't installed, so using the built-in database this time."
  echo "To use Docker instead: install Docker Desktop (https://www.docker.com/products/docker-desktop/),"
  echo "open it once, then run this again. Data doesn't move between the two."
  USE_DOCKER=0
fi
if [ "$USE_DOCKER" = 1 ]; then
  if ! docker info >/dev/null 2>&1; then
    bold "Starting Docker Desktop…"
    open -a Docker 2>/dev/null || true
    for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
    if ! docker info >/dev/null 2>&1; then
      echo "Docker did not start. Open Docker Desktop, wait until it says it is running, then run this again."
      echo "(Or run with --no-docker to use the built-in database.)"
      exit 1
    fi
  fi
  bold "Starting Postgres in Docker…"
  docker compose up -d --wait
  export DATABASE_URL="postgresql+psycopg://agent:agent@localhost:5432/agent"
else
  bold "Starting the built-in Postgres (first run downloads it)…"
  mkdir -p "$ROOT/.local-db"
  URI="$(uv run --quiet --no-project --python 3.12 --with pgserver python scripts/local_db.py "$ROOT/.local-db/pgdata" 2>/dev/null | tail -1)"
  case "$URI" in
    postgresql://*) export DATABASE_URL="postgresql+psycopg://${URI#postgresql://}" ;;
    *) echo "Could not start the built-in database. Install Docker Desktop and run this again."; exit 1 ;;
  esac
fi

# 4. Backend dependencies and tables
bold "Installing backend dependencies (first run takes a minute)…"
uv sync --quiet
if [ "$RESET" = 1 ]; then
  uv run --quiet agi-agent reset-db --yes
fi
uv run --quiet alembic upgrade head

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

# 5. Backend API on :8000 (it also runs the Telegram and Slack listeners)
if grep -Eq '^ANTHROPIC_API_KEY=.+' .env && ! grep -Eq '^FAKE_LLM=true' .env; then
  bold "Brain: real Claude replies (spend cap $(grep '^MAX_SPEND_USD=' .env | cut -d= -f2) US dollars)."
else
  bold "Brain: offline mode. Add ANTHROPIC_API_KEY to .env for real replies."
fi
bold "Starting the backend API on http://127.0.0.1:$API_PORT …"
uv run --quiet agi-agent serve --port "$API_PORT" &
PIDS+=($!)
# Telegram and Slack listeners run inside the API, for tokens in .env or connected in the app.
if grep -Eq '^TELEGRAM_BOT_TOKEN=.+' .env; then bold "Telegram: connected from .env"; fi
if grep -Eq '^SLACK_BOT_TOKEN=.+' .env; then bold "Slack: connected from .env"; fi

# 6. Frontend on :3000. Vite dev server when Node 20+ is installed; otherwise the
#    prebuilt files in web/dist from a plain static server. Either way it calls the API.
NODE_OK=0
if command -v npm >/dev/null 2>&1 && node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' 2>/dev/null; then
  NODE_OK=1
fi
if [ "$NODE_OK" = 1 ]; then
  if [ ! -d web/node_modules ] || [ web/package-lock.json -nt web/node_modules/.package-lock.json ]; then
    bold "Installing frontend dependencies (first run takes a minute)…"
    (cd web && npm install --no-audit --no-fund --loglevel=error)
  fi
  bold "Starting the frontend (Vite) on http://localhost:$WEB_PORT …"
  (cd web && VITE_API_URL="http://127.0.0.1:$API_PORT" exec "$ROOT/web/node_modules/.bin/vite" --port "$WEB_PORT" --strictPort --clearScreen false) &
  PIDS+=($!)
else
  bold "Node 20+ isn't installed, so serving the prebuilt frontend on http://localhost:$WEB_PORT …"
  echo "(Install Node with: brew install node, to get the live-reloading dev server.)"
  uv run --quiet python -m http.server "$WEB_PORT" --bind 127.0.0.1 --directory web/dist >/dev/null 2>&1 &
  PIDS+=($!)
fi

# Wait for both, then open the app.
for _ in $(seq 1 60); do
  curl -fs "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && curl -fs "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1 && break
  sleep 1
done
APP_URL="http://localhost:$WEB_PORT/"
bold "Ready."
echo "  App (frontend):    $APP_URL"
echo "  API (backend):     http://127.0.0.1:$API_PORT   (interactive docs: http://127.0.0.1:$API_PORT/docs)"
echo "  Operator console:  http://localhost:$WEB_PORT/#/console?token=$TOKEN"
echo "  Forgot your password? Run: uv run agi-agent reset-password --email you@example.com"
echo "  Hire from scratch? Press Ctrl+C, then run: bash start.command --reset"
echo "Keep this window open. Ctrl+C stops everything."
open "$APP_URL" 2>/dev/null || xdg-open "$APP_URL" 2>/dev/null || true
wait "${PIDS[0]}"
