#!/bin/bash
# Try agi-agent on your Mac with one command: database, agent and dashboard.
#
#   bash ~/Desktop/agi-agent/start.command               start (Ctrl+C to stop the agent)
#   bash ~/Desktop/agi-agent/start.command --reset       wipe the data first
#   bash ~/Desktop/agi-agent/start.command --no-docker   embedded Postgres instead of Docker
#
# Postgres runs in Docker (Docker Desktop must be installed). Runs with the
# offline fake model unless you put ANTHROPIC_API_KEY in .env and set FAKE_LLM=false.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
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

# 3. Database: Postgres in Docker (use --no-docker for an embedded Postgres instead)
if [ "$USE_DOCKER" = 1 ]; then
  if ! command -v docker >/dev/null 2>&1; then
    bold "Docker is not installed."
    echo "Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
    echo "(or run: brew install --cask docker), open it once, then run this script again."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    bold "Starting Docker Desktop…"
    open -a Docker 2>/dev/null || true
    for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
    if ! docker info >/dev/null 2>&1; then
      echo "Docker did not start. Open Docker Desktop, wait until it says it is running, then run this again."
      exit 1
    fi
  fi
  bold "Starting Postgres in Docker…"
  docker compose up -d --wait
  export DATABASE_URL="postgresql+psycopg://agent:agent@localhost:5432/agent"
else
  bold "Starting an embedded Postgres (no Docker; first run downloads it)…"
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
if [ "$RESET" = 1 ]; then
  uv run --quiet agi-agent reset-db --yes
fi
uv run --quiet alembic upgrade head
uv run --quiet agi-agent seed-demo

# 5. Dashboard: prebuilt in web/dist; rebuild only if missing and Node exists
if [ ! -f web/dist/index.html ] && command -v npm >/dev/null 2>&1; then
  bold "Building the dashboard…"
  (cd web && npm install --no-audit --no-fund && npm run build)
fi

# 6. Run: the API and dashboard, plus Telegram and Slack if their tokens are in .env
PORT=8000
if command -v lsof >/dev/null 2>&1; then
  while lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do PORT=$((PORT + 1)); done
fi
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if grep -Eq '^ANTHROPIC_API_KEY=.+' .env && ! grep -Eq '^FAKE_LLM=true' .env; then
  bold "Brain: real Claude replies (spend cap $(grep '^MAX_SPEND_USD=' .env | cut -d= -f2) US dollars)."
else
  bold "Brain: offline echo mode. Add ANTHROPIC_API_KEY to .env for real replies."
fi
if grep -Eq '^TELEGRAM_BOT_TOKEN=.+' .env; then
  bold "Connecting Telegram…"
  uv run --quiet agi-agent telegram-poll &
  PIDS+=($!)
fi
if grep -Eq '^SLACK_BOT_TOKEN=.+' .env && grep -Eq '^SLACK_APP_TOKEN=.+' .env; then
  bold "Connecting Slack…"
  uv run --quiet agi-agent slack-socket &
  PIDS+=($!)
fi

URL="http://127.0.0.1:$PORT/#token=$TOKEN"
bold "Agent running. Dashboard: http://127.0.0.1:$PORT  (keep this window open; Ctrl+C to stop)"
echo "Admin token: $TOKEN"
( sleep 3; open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true ) &
uv run agi-agent serve --port "$PORT"
