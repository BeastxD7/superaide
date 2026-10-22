#!/bin/bash
# Run Superaide on your Mac with one command (for developers: see the Makefile).
#
#   bash ~/Desktop/superaide/start.command               start (Ctrl+C stops everything)
#   bash ~/Desktop/superaide/start.command --reset       wipe the data first
#   bash ~/Desktop/superaide/start.command --no-docker   embedded Postgres instead of Docker
#
# Three parts, the usual way:
#   database  Postgres 17 + pgvector in Docker (or an embedded Postgres without Docker)
#   backend/  FastAPI, a JSON API only, on http://127.0.0.1:8000  (docs at /docs)
#   frontend/ Next.js on http://localhost:3000; the browser calls /api there, which forwards to the API
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

# 0. Stop a copy that is already running: this folder's, or the older agi-agent
#    folder's. Only processes on our ports that belong to those folders are
#    stopped; anything else on the ports is reported, never killed.
ours() {
  local pid="$1" cmd cwd
  cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  case "$cmd" in *"superaide serve"*|*"agi-agent serve"*|*"$ROOT/"*) return 0 ;; esac
  case "$cwd" in "$ROOT"|"$ROOT/"*|*/agi-agent|*/agi-agent/*) return 0 ;; esac
  return 1
}
# Channel listeners from the old copy don't hold a port; match them exactly.
pkill -f "agi-agent (telegram-poll|slack-socket)" 2>/dev/null || true
if command -v lsof >/dev/null 2>&1; then
  STOPPED=0
  for port in "$API_PORT" "$WEB_PORT"; do
    for pid in $(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null); do
      if ours "$pid"; then
        parent="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
        kill "$pid" 2>/dev/null || true
        if [ -n "$parent" ] && [ "$parent" != 1 ] && ours "$parent"; then kill "$parent" 2>/dev/null || true; fi
        STOPPED=1
      fi
    done
  done
  if [ "$STOPPED" = 1 ]; then
    bold "Stopped the copy that was already running."
    sleep 2
  fi
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

# 2. backend/.env with an admin token
ENV_FILE="$ROOT/backend/.env"
new_token() { openssl rand -hex 24 2>/dev/null || date +%s%N | shasum | cut -c1-48; }
if [ ! -f "$ENV_FILE" ]; then
  bold "Creating backend/.env (offline model, no API spend)…"
  cp "$ROOT/backend/.env.example" "$ENV_FILE"
fi
if grep -q '^ADMIN_TOKEN=$' "$ENV_FILE"; then
  TOKEN="$(new_token)"
  tmp="$(mktemp)"
  sed -e "s|^ADMIN_TOKEN=\$|ADMIN_TOKEN=$TOKEN|" -e "s|^SLACK_TEAM_ID=\$|SLACK_TEAM_ID=T_DEMO|" "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
fi
TOKEN="$(grep '^ADMIN_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
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
  # The older agi-agent copy ran its own database on the same port: stop it (its data stays).
  OLD_DB="$(docker ps -q --filter label=com.docker.compose.project=agi-agent 2>/dev/null || true)"
  if [ -n "$OLD_DB" ]; then
    bold "Stopping the older agi-agent database so this one can use port 5432 (its data is kept)…"
    docker stop $OLD_DB >/dev/null
  fi
  bold "Starting Postgres in Docker…"
  docker compose up -d --wait db
  export DATABASE_URL="postgresql+psycopg://agent:agent@localhost:5432/agent"
else
  bold "Starting the built-in Postgres (first run downloads it)…"
  mkdir -p "$ROOT/.local-db"
  URI="$(uv run --quiet --no-project --python 3.12 --with pgserver python backend/scripts/local_db.py "$ROOT/.local-db/pgdata" 2>/dev/null | tail -1)"
  case "$URI" in
    postgresql://*) export DATABASE_URL="postgresql+psycopg://${URI#postgresql://}" ;;
    *) echo "Could not start the built-in database. Install Docker Desktop and run this again."; exit 1 ;;
  esac
fi

# 4. Backend dependencies and tables
bold "Installing backend dependencies (first run takes a minute)…"
cd "$ROOT/backend"
uv sync --quiet
if [ "$RESET" = 1 ]; then
  uv run --quiet superaide reset-db --yes
fi
uv run --quiet alembic upgrade head
cd "$ROOT"

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

# 5. Backend API on :8000 (it also runs the Telegram and Slack listeners)
if grep -Eq '^ANTHROPIC_API_KEY=.+' "$ENV_FILE" && ! grep -Eq '^FAKE_LLM=true' "$ENV_FILE"; then
  bold "Brain: real Claude replies (spend cap $(grep '^MAX_SPEND_USD=' "$ENV_FILE" | cut -d= -f2) US dollars)."
else
  bold "Brain: offline until you connect Claude in the app (Connections)."
fi
bold "Starting the backend API on http://127.0.0.1:$API_PORT …"
(cd "$ROOT/backend" && exec uv run --quiet superaide serve --port "$API_PORT" --reload) &
PIDS+=($!)
# Telegram and Slack listeners run inside the API, for tokens in .env or connected in the app.
if grep -Eq '^TELEGRAM_BOT_TOKEN=.+' "$ENV_FILE"; then bold "Telegram: connected from backend/.env"; fi
if grep -Eq '^SLACK_BOT_TOKEN=.+' "$ENV_FILE"; then bold "Slack: connected from backend/.env"; fi

# 6. Frontend: Next.js on :3000. It needs Node 20.9+; without it, a private copy
#    of Node (checked against nodejs.org's published checksums) goes into .tools/.
node_ok() { command -v node >/dev/null 2>&1 && node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>20||(a===20&&b>=9)?0:1)' 2>/dev/null; }
if ! node_ok && [ -x "$ROOT/.tools/node/bin/node" ]; then export PATH="$ROOT/.tools/node/bin:$PATH"; fi
if ! node_ok; then
  bold "Installing Node.js 24 for the frontend (one time, into .tools/)…"
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) PLATFORM=darwin-arm64 ;;
    Darwin-x86_64) PLATFORM=darwin-x64 ;;
    Linux-x86_64) PLATFORM=linux-x64 ;;
    Linux-aarch64) PLATFORM=linux-arm64 ;;
    *) echo "Install Node.js 24 from https://nodejs.org and run this again."; exit 1 ;;
  esac
  DIST=https://nodejs.org/dist/latest-v24.x
  SUMS="$(curl -fsSL "$DIST/SHASUMS256.txt")"
  FILE="$(printf '%s\n' "$SUMS" | awk '{print $2}' | grep -E "^node-v24\.[0-9.]+-$PLATFORM\.tar\.gz$" | head -1)"
  [ -n "$FILE" ] || { echo "Couldn't find Node for $PLATFORM. Install it from https://nodejs.org."; exit 1; }
  TMP="$(mktemp -d)"
  curl -fsSL "$DIST/$FILE" -o "$TMP/$FILE"
  WANT="$(printf '%s\n' "$SUMS" | awk -v f="$FILE" '$2 == f {print $1}')"
  GOT="$(shasum -a 256 "$TMP/$FILE" 2>/dev/null || sha256sum "$TMP/$FILE")"
  [ "${GOT%% *}" = "$WANT" ] || { echo "Node download failed its checksum; not installing it."; exit 1; }
  mkdir -p "$ROOT/.tools/node"
  tar -xzf "$TMP/$FILE" -C "$ROOT/.tools/node" --strip-components 1
  export PATH="$ROOT/.tools/node/bin:$PATH"
fi
if [ ! -d frontend/node_modules ] || [ frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]; then
  bold "Installing frontend dependencies (first run takes a minute)…"
  (cd frontend && npm ci --no-audit --no-fund --loglevel=error)
fi
bold "Starting the frontend (Next.js) on http://localhost:$WEB_PORT …"
# On the same Wi-Fi, a phone can open the app at the Mac's address (dev only, plain http).
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [ -n "$LAN_IP" ]; then
  export DEV_ORIGINS="${DEV_ORIGINS:+$DEV_ORIGINS,}$LAN_IP"
  echo "On your phone (same Wi-Fi): http://$LAN_IP:$WEB_PORT"
fi
(cd frontend && node scripts/copy-vad-assets.mjs >/dev/null 2>&1 || true)  # the voice call's detector files
(cd frontend && API_INTERNAL_URL="http://127.0.0.1:$API_PORT" exec ./node_modules/.bin/next dev --port "$WEB_PORT") &
PIDS+=($!)

# Wait for both, then open the app.
for _ in $(seq 1 120); do
  curl -fs "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && curl -fs "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1 && break
  sleep 1
done
APP_URL="http://localhost:$WEB_PORT/"
bold "Ready."
echo "  App (frontend):    $APP_URL"
echo "  API (backend):     http://127.0.0.1:$API_PORT   (interactive docs: http://127.0.0.1:$API_PORT/docs)"
echo "  Operator console:  http://localhost:$WEB_PORT/console?token=$TOKEN"
echo "  Forgot your password? Run: (cd backend && uv run superaide reset-password --email you@example.com)"
echo "  Hire from scratch? Press Ctrl+C, then run: bash start.command --reset"
echo "Keep this window open. Ctrl+C stops everything."
open "$APP_URL" 2>/dev/null || xdg-open "$APP_URL" 2>/dev/null || true
wait "${PIDS[0]}"
