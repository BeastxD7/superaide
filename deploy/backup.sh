#!/usr/bin/env bash
# Nightly database backup for docker-compose.prod.yml: a compressed pg_dump,
# kept for KEEP_DAYS days. Run from anywhere; cron example (deploy/README.md):
#   15 3 * * * /opt/superaide/deploy/backup.sh >> /var/log/superaide-backup.log 2>&1
#
# The dump holds the stored credentials only in encrypted form. Keep a copy of
# .env (SECRETS_MASTER_KEY) somewhere else, or a restore can't decrypt them.
#
# Settings (environment): BACKUP_DIR (default <repo>/backups), KEEP_DAYS (14),
# COMPOSE_PROJECT_NAME (only to target another stack, e.g. a test one).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
compose() { docker compose --project-directory "$ROOT" -f "$ROOT/docker-compose.prod.yml" "$@"; }

umask 077
mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/superaide-$stamp.dump"

# -Fc: compressed, restorable table by table with pg_restore. Written to a temp
# name first so a failed dump never looks like a good one.
compose exec -T db pg_dump -U agent -d agent -Fc --no-owner > "$out.partial"
mv "$out.partial" "$out"
echo "$(date -u +%FT%TZ) backup ok: $out ($(du -h "$out" | cut -f1))"

find "$BACKUP_DIR" -name 'superaide-*.dump' -type f -mtime +"$KEEP_DAYS" -print -delete
