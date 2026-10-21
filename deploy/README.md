# Deploying Superaide to a VPS

This guide puts the whole app (web app, API, database) on one Linux server, reachable at
`https://your-domain` with a certificate from Let's Encrypt. It works on any Ubuntu or Debian
VPS, Bluehost included. Plan on 30 minutes, most of it waiting for DNS and the first build.

```
 browser ──HTTPS──▶ Caddy :443 ──▶ web (Next.js) :3000 ──/api/*──▶ api (FastAPI) :8000 ──▶ Postgres
                    certificates,     pages, and the same-       migrations on start,    private network,
                    headers, rate     origin /api proxy          job runner, Telegram/   no route out
                    limits                                       Slack listeners
```

Only Caddy is published (ports 80 and 443). Everything else talks on private Docker networks.
The files: `docker-compose.prod.yml`, `.env.prod.example`, `deploy/caddy/` (Caddyfile and image),
`deploy/bootstrap.sh` (automated setup) and `deploy/backup.sh` (nightly dump).

## What you need

- **A VPS** with Ubuntu 24.04 or Debian 12 and root (SSH) access.
  - **2 vCPU / 4 GB RAM / 40 GB disk** is comfortable.
  - 2 GB works, but building the web app on the server then leans on swap, so builds are slow. The bootstrap script adds swap automatically.
  - The whole stack idles at about 250 MB.
- **A domain or subdomain** you control, e.g. `ava.example.com`.
- **Optional:** a Claude API key, a Telegram bot and a Slack app. You can also add all of them later in the app.

## 1. Point the domain at the server

At your DNS provider, add an **A record**: name `ava` (or `@` for the bare domain), value = the
server's public IPv4 address. If you also have IPv6, add an **AAAA** record; otherwise make sure
none points elsewhere. Check from your computer (this can take a few minutes):

```bash
dig +short ava.example.com      # must print the server's IP
```

Caddy can only get a certificate once this resolves to the server.

## 2. Log in and secure SSH

```bash
ssh root@SERVER_IP
```

Recommended before anything else:

1. Use an SSH key, not a password (`ssh-copy-id root@SERVER_IP` from your computer).
2. Set `PasswordAuthentication no` in `/etc/ssh/sshd_config`.
3. Run `systemctl restart ssh`.
4. Turn on automatic security updates: `apt install -y unattended-upgrades`.

## 3. Get the code

The repository is private, so the server needs read access. The simplest way is a read-only
**deploy key**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/superaide_deploy -N ""
cat ~/.ssh/superaide_deploy.pub
```

Add the printed key on GitHub (repo → Settings → Deploy keys, read-only), then clone it:

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/superaide_deploy" git clone git@github.com:BeastxD7/superaide.git /opt/superaide
cd /opt/superaide
git config core.sshCommand "ssh -i ~/.ssh/superaide_deploy"
```

## 4a. Automatic setup (recommended)

```bash
sudo bash deploy/bootstrap.sh ava.example.com
```

The script does the following:

- installs Docker Engine and the compose plugin from Docker's repository
- sets the `ufw` firewall to allow only SSH, 80 and 443
- adds 2 GB of swap on machines with less than 4 GB of memory
- creates `.env` with a fresh database password, vault master key and admin token
- installs the nightly backup
- builds and starts everything

It's safe to run again: existing secrets are kept, and finished steps are skipped. Jump to
step 5.

## 4b. Manual setup

```bash
# Docker (official instructions: https://docs.docker.com/engine/install/ubuntu/)
curl -fsSL https://get.docker.com | sh

# Firewall
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp && ufw --force enable

# Settings
cp .env.prod.example .env && chmod 600 .env
nano .env
#   DOMAIN=ava.example.com
#   POSTGRES_PASSWORD=   ← openssl rand -hex 24
#   SECRETS_MASTER_KEY=  ← openssl rand -base64 32
#   ADMIN_TOKEN=         ← openssl rand -hex 32

# Start (the first build takes 3 to 10 minutes)
docker compose -f docker-compose.prod.yml up -d --build --wait
```

> **ufw and Docker:** ports that Docker publishes bypass ufw rules. That's fine here, because
> the only published ports are Caddy's 80 and 443. Never add a `ports:` entry to `db`, `api` or
> `web` in the compose file: it would be reachable from the internet whatever ufw says.

## 5. First visit: claim Ava straight away

Open `https://ava.example.com`, **sign up, and hire Ava**. The first account to hire Ava becomes
the owner, and the site is public, so do this before sharing the address.

Then, in the workspace, connect Claude, Telegram and Slack under Connections. They're checked
with each service and stored encrypted with `SECRETS_MASTER_KEY`.

**Operator console:** `https://ava.example.com/console`. It asks for `ADMIN_TOKEN` from `.env`.

## Channels, Google and the API docs on a server

- **Telegram:** uses polling (the API asks Telegram for new messages). It needs no inbound URL and
  works behind any firewall. Only one process may poll a bot, so don't run the same bot token
  elsewhere at the same time, e.g. on your Mac.
  - Webhook mode is possible: set `TELEGRAM_WEBHOOK_SECRET`, uncomment the `/webhooks/*` block in `deploy/caddy/Caddyfile`, rebuild, and register `https://DOMAIN/webhooks/telegram` with Telegram.
  - Polling is the tested path.
- **Slack:** uses Socket Mode (an outbound websocket), so it needs no inbound URL either.
- **Google Calendar:** in your Google Cloud OAuth client ("Web application"), add this authorised
  redirect URI exactly: `https://ava.example.com/api/app/connections/google/callback`
- **Background jobs** (reminders, follow-ups, digests) and the listeners run inside the API.
  - That's why the API runs as a single process (`--workers 1`). More workers would poll Telegram twice and send reminders twice.
  - One process is plenty for one owner.
- **`/docs`** (the interactive API reference) is off in production (`ENABLE_API_DOCS=false`), and
  the web app's proxy only forwards `/api/auth`, `/api/app`, `/api/api` (console, needs
  `ADMIN_TOKEN`) and `/api/health`.
- **The simulator** is forced off, and session cookies are `Secure`. The compose file fixes both
  settings, whatever `.env` says.

## Day-to-day

All commands run from `/opt/superaide`. A shortcut helps:

```bash
alias dc='docker compose -f /opt/superaide/docker-compose.prod.yml'
```

| Task | Command |
| --- | --- |
| Status | `dc ps` |
| Logs (all, follow) | `dc logs -f --tail=100` |
| Logs (one service) | `dc logs -f api` (or `web`, `caddy`, `db`) |
| Restart | `dc restart api` |
| Stop / start | `dc down` / `dc up -d` (data is kept; never add `-v`, it deletes the database) |
| Database shell | `dc exec db psql -U agent agent` |
| Spend so far | `dc exec api superaide spend` |
| Freeze Ava (kill switch) | `dc exec api superaide freeze` (and `unfreeze`) |
| Migration status | `dc exec api alembic current` |

**Logs:**

- Docker keeps 5 × 10 MB of logs per service, then rotates them.
- Caddy's access log is in `dc logs caddy`. Cookies and `Authorization` headers are redacted, and Google's OAuth `code` is removed from logged URLs.
- The API doesn't log request lines at all.

**Uploads:** files added to the notebook aren't kept on disk. Their text and the facts learned
from them live in Postgres, so the database backup covers them.

## Updates

```bash
cd /opt/superaide
git pull
docker compose -f docker-compose.prod.yml up -d --build --wait
docker image prune -f      # reclaim space from old builds
```

On every start the one-shot `migrate` service applies new database migrations as the database
owner and refreshes the app's own role (`superaide_app`); the API waits for it, then serves. Take a
backup first if the update notes mention a migration (`deploy/backup.sh`).

Installs made before October 2026 have no `APP_DB_PASSWORD` in `.env`, so the app's role uses the
`POSTGRES_PASSWORD` value. To give it its own, add `APP_DB_PASSWORD=$(openssl rand -hex 24)` to `.env`
and run the update command again.

## Backups

`bootstrap.sh` installs `/etc/cron.d/superaide-backup`. It runs `deploy/backup.sh` at 03:15
every night: a compressed `pg_dump` into `/opt/superaide/backups/`, kept for 14 days. To run it
by hand or set it up yourself:

```bash
/opt/superaide/deploy/backup.sh
echo "15 3 * * * root /opt/superaide/deploy/backup.sh >>/var/log/superaide-backup.log 2>&1" > /etc/cron.d/superaide-backup
```

These backups sit on the same server. Copy them off it regularly, for example:

```bash
rsync -a root@SERVER_IP:/opt/superaide/backups/ ~/superaide-backups/
```

Use your VPS provider's snapshot feature as well.

**Also keep `.env` somewhere safe**, such as a password manager. The database stores your Claude,
Telegram and Slack keys encrypted with `SECRETS_MASTER_KEY`, and a backup without that key can't
decrypt them.

### Restore

```bash
cd /opt/superaide
dc stop api web caddy
dc exec -T db dropdb -U agent agent
dc exec -T db createdb -U agent agent
dc exec -T db pg_restore -U agent -d agent --no-owner < backups/superaide-YYYYMMDDTHHMMSSZ.dump
dc up -d --wait
```

To move to a new server:

1. Run steps 1–4 there, but put the **old** `.env` in place before starting.
2. Restore the latest dump.
3. Point DNS at the new server.

## Rotating secrets

| Secret | How |
| --- | --- |
| `ADMIN_TOKEN` | Put a new value (`openssl rand -hex 32`) in `.env`, then run `dc up -d`. |
| `POSTGRES_PASSWORD` | Run `dc exec db psql -U agent -c "ALTER USER agent PASSWORD 'NEW'"`, put the same value in `.env`, then run `dc up -d`. |
| `APP_DB_PASSWORD` | Put the new value in `.env`, then run `dc up -d`: the `migrate` service sets it on the `superaide_app` role before the API starts. |
| Claude / Telegram / Slack keys | Reconnect them in the app (Workspace → Connections). Revoke the old one at the service. |
| `SECRETS_MASTER_KEY` | There's no re-encryption yet. Changing it makes every saved connection unreadable. Change it, run `dc up -d`, then reconnect each service in the app. Only do this if the key leaked. |
| Everyone's sessions | Run `dc exec db psql -U agent agent -c "DELETE FROM user_session"`. |

The vault reads its master key in this order:

1. `SECRETS_MASTER_KEY`
2. `SECRETS_MASTER_KEY_FILE`: a file holding the same value, e.g. a Docker secret at `/run/secrets/...`. If this is set and the file is missing, the API refuses to start rather than make a new key.
3. The macOS Keychain (development on a Mac).
4. A generated key file.

The compose file requires option 1, so a server never silently falls through to 4.

## Security summary

- **HTTPS:**
  - Let's Encrypt certificates, renewed automatically. HTTP redirects to HTTPS.
  - HSTS for a year, plus `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` and `Cross-Origin-Opener-Policy`.
- **Rate limits (Caddy):**
  - 20 sign-up / log-in / password POSTs per minute per address, and 600 requests per minute per address overall. Over that, the response is a 429.
  - These sit on top of the app's per-account lockout (5 wrong passwords → 15 minutes) and per-address limits.
  - The client address reaches the API intact: Caddy sets it, and the web app passes it on (`TRUST_PROXY_HEADERS=true`).
- **Containers:**
  - All four containers run as non-root users.
  - API, web and Caddy have read-only filesystems, no Linux capabilities and `no-new-privileges`.
  - Postgres sits on an internal network with no route out and no published port.
- **Database:**
  - The API connects as `superaide_app`, a role that can read and write rows but can't change the schema, turn security off or bypass it. Migrations run as the owner, in the separate `migrate` container.
  - Row-Level Security in Postgres keeps each hired Ava's rows apart, as a second line behind the app's own checks (ADR 0009).
- **Secrets:**
  - Secrets live only in `.env` (mode 600). The images contain none.
  - The API doesn't log request lines or echo credentials in errors.

## Bluehost VPS notes

- **Pick a plain OS image.** Bluehost's VPS plans have often come with cPanel/WHM, which runs
  Apache on ports 80 and 443 and usually uses AlmaLinux (`dnf` and `firewalld`, not `ufw`).
  - If you can choose, reinstall the VPS with a **bare Ubuntu 24.04** (or Debian 12) image, without a control panel. Then everything above applies as written.
  - If you must keep cPanel, stop its web server first: `systemctl disable --now httpd`. Note that cPanel sites on the same server stop working.
  - On AlmaLinux, install Docker from Docker's RHEL/CentOS instructions. Open the ports with `firewall-cmd --permanent --add-service=http --add-service=https && firewall-cmd --reload` instead of ufw. `bootstrap.sh` refuses to run there; use the manual steps.
- **Root and SSH access:** enable root SSH or create a sudo user from the Bluehost control panel
  (Server → Root access / SSH keys). Then follow step 2.
- **Ports:** some hosts block outbound port 25 (mail); Superaide doesn't use it. Inbound 80 and
  443 must be open. If Bluehost has a network firewall in its panel, allow them there too.
- **Check what you have:** `cat /etc/os-release` shows the OS. `ss -ltnp | grep -E ':(80|443) '`
  shows whether something already holds the web ports.

## Troubleshooting

- **The certificate isn't issued:**
  - Check `dc logs caddy | grep -i acme`. Usually DNS doesn't point here yet, or port 80 is blocked (Let's Encrypt checks over port 80).
  - Caddy retries by itself. Let's Encrypt limits failed attempts, so fix DNS first.
- **502 "Can't reach the Superaide API":** the API is still starting or crashed. Check
  `dc logs api`. A bad `SECRETS_MASTER_KEY` or a failed migration shows there.
- **Logged out right after signing up:** the site must be opened over `https://` at the exact
  `DOMAIN`, because the session cookie is `Secure`.
- **Testing the production stack on your own computer:**
  - Use a throwaway `.env` with `DOMAIN=localhost`, `HTTP_PUBLISH=127.0.0.1:18080`, `HTTPS_PUBLISH=127.0.0.1:18443` and `PUBLIC_URL=https://localhost:18443`.
  - Run `docker compose -p superaide-prodtest -f docker-compose.prod.yml up -d --build`, then open `https://localhost:18443`. Caddy uses its own local certificate there; accept the browser warning.
  - Remove it with `docker compose -p superaide-prodtest -f docker-compose.prod.yml down -v`.
