#!/bin/bash
# Run the latest Superaide on this Mac and share it on the internet with a Cloudflare tunnel.
#
#   bash share.sh                     quick tunnel: a random https://<words>.trycloudflare.com address, free, no account
#   bash share.sh --no-update         do not pull the latest code first
#   bash share.sh --reset             wipe the data first (passed to start.command)
#   bash share.sh --no-docker         embedded Postgres instead of Docker (passed to start.command)
#   bash share.sh --hostname app.example.com --tunnel superaide
#                                     a STABLE address: a named Cloudflare tunnel you created once with
#                                     `cloudflared tunnel create superaide` + `cloudflared tunnel route dns superaide app.example.com`
#   bash share.sh --hostname app.example.com --tunnel-token <token>
#                                     the same for a tunnel managed in the Cloudflare dashboard (uses a token instead)
#   bash share.sh --ngrok-domain your-name.ngrok-free.app
#                                     a STABLE free address from ngrok (one free static domain per account, see below)
#
# What it does:
#   1. fast-forwards this checkout to the latest main (git pull --ff-only; skipped with local changes or --no-update)
#   2. starts the tunnel to the app and waits for its public address
#   3. starts the database, API and website exactly like start.command, told to accept that address
#      (LINK_ORIGINS, DEV_ORIGINS, PUBLIC_BASE_URL), then prints what to share
#
# Ports: the app is on http://localhost:$WEB_PORT (default 3000) and the API on $API_PORT (default 8010,
# not 8000, which other tools often use). Override: WEB_PORT=3100 API_PORT=8110 bash share.sh
#
# GOOGLE SIGN-IN, CALENDAR AND GMAIL over a tunnel
#   Google only accepts redirect addresses you registered in advance, with no wildcards. A quick tunnel gets a NEW
#   address every time, so Google would need a new entry every run. Three ways around it:
#     1. Use Google sign-in yourself at http://localhost:$WEB_PORT (registered once, never changes). People you share
#        the link with sign up with email and password, which works over any address. (Simplest.)
#     2. A stable address: make a named Cloudflare tunnel once (needs a free Cloudflare account and a domain on it):
#          cloudflared tunnel login
#          cloudflared tunnel create superaide
#          cloudflared tunnel route dns superaide app.example.com
#          cloudflared tunnel token superaide        (prints the token)
#        then: bash share.sh --hostname app.example.com --tunnel-token <that token>
#        Register https://app.example.com/api/auth/google/callback and
#        https://app.example.com/api/app/connections/google/callback in Google Cloud once; it never changes.
#     3. Keep the quick tunnel and add the two addresses this script prints to Google Cloud each run (about a minute).
#     4. A free fixed address from ngrok, no domain needed: sign up at ngrok.com, `brew install ngrok`,
#        `ngrok config add-authtoken <your token>`, claim your free static domain in the ngrok dashboard
#        (Universal Gateway > Domains), then: bash share.sh --ngrok-domain your-name.ngrok-free.app
#        Register https://your-name.ngrok-free.app/api/auth/google/callback and
#        https://your-name.ngrok-free.app/api/app/connections/google/callback in Google Cloud once.
#   FRIENDS CONNECTING THEIR OWN GOOGLE: while your Google app is in "Testing", add each friend's Google address under
#   OAuth consent screen > Test users (up to 100). They will see "Google hasn't verified this app": Advanced > continue.
#   In Testing mode Google also expires their connection after 7 days (they reconnect). Publishing the app removes the
#   test-user list; Gmail and Calendar scopes then need Google's verification review.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
export WEB_PORT="${WEB_PORT:-3000}"
export API_PORT="${API_PORT:-8010}"
bold() { printf "\n\033[1m%s\033[0m\n" "$*"; }

UPDATE=1; HOSTNAME_ARG=""; TOKEN_ARG=""; TUNNEL_NAME=""; NGROK_DOMAIN=""; PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-update) UPDATE=0 ;;
    --hostname) HOSTNAME_ARG="${2:?--hostname needs a value}"; shift ;;
    --tunnel) TUNNEL_NAME="${2:?--tunnel needs a tunnel name}"; shift ;;
    --tunnel-token) TOKEN_ARG="${2:?--tunnel-token needs a value}"; shift ;;
    --ngrok-domain) NGROK_DOMAIN="${2:?--ngrok-domain needs a value}"; shift ;;
    --reset|--no-docker) PASS+=("$1") ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done
if [ -n "$HOSTNAME_ARG" ] && [ -z "$TOKEN_ARG" ] && [ -z "$TUNNEL_NAME" ]; then
  echo "--hostname needs --tunnel <name> (a tunnel made with cloudflared tunnel create) or --tunnel-token <token>."; exit 2
fi
if [ -z "$HOSTNAME_ARG" ] && { [ -n "$TOKEN_ARG" ] || [ -n "$TUNNEL_NAME" ]; }; then
  echo "--tunnel and --tunnel-token need --hostname."; exit 2
fi

# 1. Latest code (never touches your changes: only a clean fast-forward)
if [ "$UPDATE" = 1 ] && git rev-parse --git-dir >/dev/null 2>&1; then
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "You have local changes, so not updating the code."
  elif git pull --ff-only --quiet 2>/dev/null; then
    bold "Code is at $(git log -1 --format='%h %s' | cut -c1-70)"
  else
    echo "Could not fast-forward (offline, detached HEAD or a diverged branch): running the code as it is."
  fi
fi

if [ -n "$NGROK_DOMAIN" ] && [ -n "$HOSTNAME_ARG" ]; then echo "Use either --ngrok-domain or --hostname, not both."; exit 2; fi

# 2. cloudflared (or ngrok)
if [ -z "$NGROK_DOMAIN" ] && ! command -v cloudflared >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    bold "Installing cloudflared with Homebrew (one time)…"
    brew install cloudflared
  else
    echo "Install cloudflared first: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
  fi
fi

TUNNEL_LOG="$(mktemp -t superaide-tunnel.XXXXXX)"
TUNNEL_PID=""
cleanup() { [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true; rm -f "$TUNNEL_LOG"; }
trap cleanup EXIT INT TERM

if [ -n "$NGROK_DOMAIN" ]; then
  command -v ngrok >/dev/null 2>&1 || { echo "Install ngrok first: brew install ngrok, then: ngrok config add-authtoken <token>"; exit 1; }
  PUBLIC_URL="https://$NGROK_DOMAIN"
  bold "Starting the ngrok tunnel for $PUBLIC_URL …"
  ngrok http "http://localhost:$WEB_PORT" --url "$NGROK_DOMAIN" --log stdout >"$TUNNEL_LOG" 2>&1 &
  TUNNEL_PID=$!
  for _ in $(seq 1 20); do
    grep -qi 'started tunnel\|url=https' "$TUNNEL_LOG" && break
    kill -0 "$TUNNEL_PID" 2>/dev/null || { echo "ngrok stopped:"; tail -6 "$TUNNEL_LOG"; exit 1; }
    sleep 1
  done
elif [ -n "$HOSTNAME_ARG" ]; then
  # A named tunnel: the public hostname and its route to http://localhost:$WEB_PORT are set up in Cloudflare
  # (Zero Trust > Networks > Tunnels > your tunnel > Public hostname), or by `cloudflared tunnel route dns`.
  PUBLIC_URL="https://$HOSTNAME_ARG"
  bold "Starting your named tunnel for $PUBLIC_URL …"
  if [ -n "$TUNNEL_NAME" ]; then
    # A tunnel made with the cloudflared command line: its credentials are in ~/.cloudflared and the route to this
    # Mac's site is given here, so nothing else is needed in the Cloudflare dashboard.
    cloudflared tunnel --no-autoupdate run --url "http://localhost:$WEB_PORT" "$TUNNEL_NAME" >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!
  else
    cloudflared tunnel --no-autoupdate run --token "$TOKEN_ARG" >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!
    echo "(In Cloudflare, the public hostname $HOSTNAME_ARG must point to http://localhost:$WEB_PORT.)"
  fi
else
  bold "Opening a quick Cloudflare tunnel to http://localhost:$WEB_PORT …"
  cloudflared tunnel --no-autoupdate --url "http://localhost:$WEB_PORT" >"$TUNNEL_LOG" 2>&1 &
  TUNNEL_PID=$!
  PUBLIC_URL=""
  for _ in $(seq 1 45); do
    PUBLIC_URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)"
    [ -n "$PUBLIC_URL" ] && break
    kill -0 "$TUNNEL_PID" 2>/dev/null || { echo "cloudflared stopped:"; tail -5 "$TUNNEL_LOG"; exit 1; }
    sleep 1
  done
  [ -n "$PUBLIC_URL" ] || { echo "No tunnel address after 45 seconds:"; tail -8 "$TUNNEL_LOG"; exit 1; }
fi
PUBLIC_HOST="${PUBLIC_URL#https://}"

# 3. Tell the app about its public address (these override backend/.env for this run only)
export PUBLIC_BASE_URL="$PUBLIC_URL"
export LINK_ORIGINS="${LINK_ORIGINS:+$LINK_ORIGINS,}$PUBLIC_URL"
export DEV_ORIGINS="${DEV_ORIGINS:+$DEV_ORIGINS,}$PUBLIC_HOST"
export NO_OPEN=1

bold "Public address: $PUBLIC_URL"
echo "  Share this link. Anyone who has it can open the app and sign up, so close this window when you are done."
echo
echo "  Google sign-in / Calendar / Gmail: add these two redirect URIs to your OAuth client in Google Cloud"
echo "  (APIs & Services > Credentials), unless you already use a stable --hostname:"
echo "    $PUBLIC_URL/api/auth/google/callback"
echo "    $PUBLIC_URL/api/app/connections/google/callback"
echo "  Or skip that: use Google sign-in yourself at http://localhost:$WEB_PORT (already registered)."

# Hand over to start.command (database, API, website). The tunnel stops when this window closes.
bash "$ROOT/start.command" ${PASS[@]+"${PASS[@]}"}
