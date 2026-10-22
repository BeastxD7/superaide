#!/usr/bin/env bash
# One-shot setup of a fresh Ubuntu/Debian VPS for Superaide. Safe to run again:
# every step checks before it changes anything, and existing secrets are kept.
#
#   sudo bash deploy/bootstrap.sh ava.example.com
#
# It installs Docker, opens only SSH/80/443 in the firewall, adds swap on small
# machines, writes .env with freshly generated secrets, installs the nightly
# backup, and starts the stack. Run it from inside the cloned repository.
# Skip parts with SKIP_DOCKER=1, SKIP_FIREWALL=1, SKIP_SWAP=1, SKIP_CRON=1, SKIP_START=1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
DOMAIN_ARG="${1:-}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root: sudo bash deploy/bootstrap.sh <domain>"
[[ -f "$ROOT/docker-compose.prod.yml" ]] || die "run this from the cloned superaide repository"
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}:${ID_LIKE:-}" in
  ubuntu:* | debian:* | *:*debian*) ;;
  *) die "this script supports Ubuntu and Debian (found ${PRETTY_NAME:-unknown}). See deploy/README.md for the manual steps." ;;
esac

# ------------------------------------------------------------------ Docker
if [[ "${SKIP_DOCKER:-}" != 1 ]]; then
  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    say "Docker is installed ($(docker --version))"
  else
    say "Installing Docker Engine and the compose plugin (from Docker's apt repository)"
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg openssl git
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID ${VERSION_CODENAME} stable" \
      >/etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  systemctl enable --now docker >/dev/null
fi
command -v openssl >/dev/null || { apt-get update -qq && apt-get install -y -qq openssl; }

# ------------------------------------------------------------------ Firewall
if [[ "${SKIP_FIREWALL:-}" != 1 ]]; then
  say "Firewall: allow SSH, HTTP and HTTPS only"
  command -v ufw >/dev/null || { apt-get update -qq && apt-get install -y -qq ufw; }
  ufw allow OpenSSH >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw allow 443/udp >/dev/null # HTTP/3
  ufw --force enable >/dev/null
  ufw status | sed 's/^/    /'
fi

# ------------------------------------------------------------------ Swap
# Building the web app needs about 2 GB of memory; give small servers room.
if [[ "${SKIP_SWAP:-}" != 1 ]]; then
  mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  if [[ $mem_mb -lt 3800 ]] && ! swapon --show | grep -q .; then
    say "Adding a 2 GB swap file (this machine has ${mem_mb} MB of memory)"
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  fi
fi

# ------------------------------------------------------------------ .env
say "Settings in $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/.env.prod.example" "$ENV_FILE"
  echo "    created from .env.prod.example"
fi
chmod 600 "$ENV_FILE"

current() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1; }
# Set KEY=VALUE in .env, replacing an existing line or adding one.
put() {
  local key=$1 value=$2 tmp
  tmp=$(mktemp)
  if grep -q "^$key=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$value" 'BEGIN { FS = OFS = "=" } $1 == k { print k "=" v; next } { print }' "$ENV_FILE" >"$tmp"
  else
    cat "$ENV_FILE" >"$tmp" && echo "$key=$value" >>"$tmp"
  fi
  cat "$tmp" >"$ENV_FILE" && rm -f "$tmp"
}
# Generate a secret only if it is still empty: re-running never replaces one.
ensure_secret() {
  if [[ -z "$(current "$1")" ]]; then
    put "$1" "$2"
    echo "    generated $1"
  else
    echo "    kept existing $1"
  fi
}

domain="$(current DOMAIN)"
if [[ -n "$DOMAIN_ARG" ]]; then
  domain="$DOMAIN_ARG"
elif [[ -z "$domain" || "$domain" == "ava.example.com" ]]; then
  read -r -p "    Domain for the app (its DNS A record points here), e.g. ava.example.com: " domain
fi
domain="${domain#http://}" && domain="${domain#https://}" && domain="${domain%%/*}"
[[ "$domain" =~ ^[A-Za-z0-9.-]+$ && "$domain" != "ava.example.com" ]] || die "'$domain' isn't a usable domain name"
put DOMAIN "$domain"
echo "    DOMAIN=$domain"

ensure_secret POSTGRES_PASSWORD "$(openssl rand -hex 24)"
ensure_secret APP_DB_PASSWORD "$(openssl rand -hex 24)"
ensure_secret SECRETS_MASTER_KEY "$(openssl rand -base64 32)"
ensure_secret ADMIN_TOKEN "$(openssl rand -hex 32)"

# ------------------------------------------------------------------ Backups
if [[ "${SKIP_CRON:-}" != 1 ]]; then
  say "Nightly database backup at 03:15 (kept 14 days in $ROOT/backups)"
  # Minimal Debian cloud images may ship without a cron daemon.
  command -v cron >/dev/null || { apt-get update -qq && apt-get install -y -qq cron && systemctl enable --now cron >/dev/null; }
  chmod +x "$ROOT/deploy/backup.sh"
  echo "15 3 * * * root $ROOT/deploy/backup.sh >>/var/log/superaide-backup.log 2>&1" >/etc/cron.d/superaide-backup
  chmod 644 /etc/cron.d/superaide-backup
fi

# ------------------------------------------------------------------ Start
if [[ "${SKIP_START:-}" != 1 ]]; then
  ip=$(curl -fsS4 --max-time 5 https://api.ipify.org || true)
  resolved=$(getent ahostsv4 "$domain" | awk 'NR == 1 { print $1 }' || true)
  if [[ -n "$ip" && "$resolved" != "$ip" ]]; then
    echo
    echo "    Warning: $domain resolves to '${resolved:-nothing}', but this server is $ip."
    echo "    HTTPS certificates can't be issued until the DNS A record points here."
  fi
  say "Building and starting (the first build takes a few minutes)"
  cd "$ROOT"
  docker compose -f docker-compose.prod.yml up -d --build --wait
  docker compose -f docker-compose.prod.yml ps
  say "Done"
  cat <<EOF
    App:              https://$domain        (sign up and hire your Ava; anyone can hire their own, unless ALLOW_MANY_OWNERS=false)
    Operator console: https://$domain/console (password: ADMIN_TOKEN in $ENV_FILE)
    Logs:             docker compose -f docker-compose.prod.yml logs -f

    Copy $ENV_FILE to a password manager now. Without SECRETS_MASTER_KEY the
    backups can't decrypt the Claude, Telegram and Slack keys you save in the app.
EOF
fi
