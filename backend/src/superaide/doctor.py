"""`superaide doctor`: one command that checks the install and says how to fix each problem (#409).

Every check is a small function that returns a `Result` (ok, warn or fail, one sentence of cause and the exact
command or setting that fixes it) and is registered with `@check`, so adding one is a few lines. Checks only read:
nothing is fixed automatically, and no secret is ever printed (messages name settings, never values).

Tests build a `Context` with their own settings, .env path and fake HTTP client and call the checks directly.
"""

from __future__ import annotations

import base64
import binascii
import difflib
import re
import socket
import stat
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from superaide.platform.config import Settings, get_settings

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    cause: str = ""  # one sentence: what is wrong
    fix: str = ""  # the exact command or setting that fixes it


@dataclass
class Context:
    settings: Settings
    env_path: Path = field(default_factory=lambda: Path(".env"))
    example_path: Path | None = None
    http: httpx.Client | None = None
    api_port: int = 8000
    environ: dict[str, str] | None = None  # the process environment; None means os.environ

    def client(self) -> httpx.Client:
        return self.http or httpx.Client(timeout=8)

    def env(self) -> dict[str, str]:
        import os

        return dict(os.environ) if self.environ is None else self.environ


CHECKS: list[tuple[str, Callable[[Context], list[Result]]]] = []


def check(name: str):
    def register(fn: Callable[[Context], list[Result]]) -> Callable[[Context], list[Result]]:
        CHECKS.append((name, fn))
        return fn

    return register


def _ok(name: str) -> list[Result]:
    return [Result(name, OK)]


def _example_names(ctx: Context) -> set[str]:
    path = ctx.example_path or Path(__file__).resolve().parents[2] / ".env.example"
    names: set[str] = set()
    if path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r"^#? ?([A-Z][A-Z0-9_]*)=", line)
            if m:
                names.add(m.group(1))
    return names


def _known_names(ctx: Context) -> set[str]:
    known = _example_names(ctx)
    for name, f in type(ctx.settings).model_fields.items():
        known.add(name.upper())
        if isinstance(f.alias, str):
            known.add(f.alias.upper())
    return known


@check("Settings file")
def env_file(ctx: Context) -> list[Result]:
    name = "Settings file (.env)"
    if not ctx.env_path.exists():
        return [
            Result(
                name,
                WARN,
                "There is no .env file, so every setting has its default.",
                "Run `cp backend/.env.example backend/.env` and edit it.",
            )
        ]
    seen: Counter[str] = Counter()
    bad_lines: list[int] = []
    for n, raw in enumerate(ctx.env_path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if not m:
            bad_lines.append(n)
            continue
        seen[m.group(1)] += 1
    out: list[Result] = []
    if bad_lines:
        lines = ", ".join(str(n) for n in bad_lines[:5])
        out.append(Result(name, FAIL, f"Line {lines} of .env is not NAME=value.", "Fix or delete that line."))
    for key, count in seen.items():
        if count > 1:
            out.append(
                Result(
                    name,
                    FAIL,
                    f"{key} appears {count} times in .env, so only one of them wins.",
                    f"Delete the extra {key} lines.",
                )
            )
    known = _known_names(ctx)
    for key in seen:
        if key.upper() not in known:
            guess = difflib.get_close_matches(key.upper(), sorted(known), n=1)
            hint = f"Did you mean {guess[0]}?" if guess else "Delete it or check the spelling against .env.example."
            out.append(Result(name, WARN, f"{key} in .env is not a setting Superaide reads. {hint}", ""))
    try:
        Settings(_env_file=ctx.env_path)  # type: ignore[call-arg]
    except Exception as exc:  # pydantic ValidationError: report which settings, never their values
        fields = sorted({str(e["loc"][0]).upper() for e in getattr(exc, "errors", lambda: [])() if e.get("loc")})
        which = ", ".join(fields) or "a setting"
        out.append(
            Result(
                name,
                FAIL,
                f"The value of {which} in .env is not valid.",
                "Compare it with the example in backend/.env.example.",
            )
        )
    return out or _ok(name)


@check("Database")
def database(ctx: Context) -> list[Result]:
    from sqlalchemy import text

    from superaide.platform.db.session import get_engine

    name = "Database reachable"
    try:
        with get_engine(ctx.settings.owner_database_url).connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:
        return [
            Result(
                name,
                FAIL,
                f"Cannot connect to the database ({type(exc).__name__}).",
                "Start it with `docker compose up -d` (or check DATABASE_URL), then run `superaide doctor` again.",
            )
        ]
    return _ok(name)


@check("Migrations")
def migrations(ctx: Context) -> list[Result]:
    from superaide.platform.security import dbcheck

    name = "Database migrated to head"
    try:
        st = dbcheck.status()
    except Exception:
        return [
            Result(
                name,
                WARN,
                "Could not read the migration state (the database may be unreachable).",
                "Fix the database check above first.",
            )
        ]
    if st.state == "behind":
        return [
            Result(name, FAIL, "The database is behind the code.", "Run `cd backend && uv run alembic upgrade head`.")
        ]
    if st.state == "unknown_revision":
        return [
            Result(
                name,
                FAIL,
                "The database is at a migration this code does not know.",
                "Update the code, or restore the matching database.",
            )
        ]
    if st.state == "multiple_heads":
        return [
            Result(
                name,
                FAIL,
                "The code has more than one migration head.",
                "Renumber or merge the newest migration (`alembic heads`).",
            )
        ]
    if st.state == "unmanaged":
        return [
            Result(
                name,
                WARN,
                "The database has no migration history (made by init-db).",
                "Run `cd backend && uv run alembic upgrade head` on a fresh database.",
            )
        ]
    return _ok(name)


@check("pgvector")
def pgvector(ctx: Context) -> list[Result]:
    from sqlalchemy import text

    from superaide.platform.db.session import get_engine

    name = "pgvector extension"
    try:
        with get_engine(ctx.settings.owner_database_url).connect() as conn:
            installed = conn.scalar(text("select count(*) from pg_extension where extname = 'vector'"))
    except Exception:
        return [
            Result(
                name,
                WARN,
                "Could not look for the extension (the database may be unreachable).",
                "Fix the database check above first.",
            )
        ]
    if installed:
        return _ok(name)
    return [
        Result(
            name,
            FAIL,
            "The pgvector extension is not installed in this database, so memory search cannot work.",
            "Use the pgvector Postgres image (`docker compose up -d`) and run `CREATE EXTENSION vector;` as a superuser.",
        )
    ]


@check("Master key")
def master_key(ctx: Context) -> list[Result]:
    from superaide.identity.vault import crypto

    name = "Master key (vault)"
    env = ctx.env()
    inline = env.get("SECRETS_MASTER_KEY", "").strip()
    file_ = env.get("SECRETS_MASTER_KEY_FILE", "").strip()
    gen = "Make one with `openssl rand -base64 32`."
    try:
        if inline:
            key = base64.b64decode(inline, validate=True)
            where = "SECRETS_MASTER_KEY"
        elif file_:
            key = base64.b64decode(Path(file_).read_text().strip(), validate=True)
            where = "SECRETS_MASTER_KEY_FILE"
        else:
            path = crypto._key_file()
            if path.exists():
                if stat.S_IMODE(path.stat().st_mode) & 0o077:
                    return [
                        Result(
                            name, FAIL, "The master key file can be read by other users.", f"Run `chmod 600 {path}`."
                        )
                    ]
                return _ok(name)
            return [
                Result(
                    name,
                    WARN,
                    "No master key is set; one is created in the keychain or a key file on first use.",
                    "For a server, set SECRETS_MASTER_KEY or SECRETS_MASTER_KEY_FILE. " + gen,
                )
            ]
    except binascii.Error, ValueError:
        return [Result(name, FAIL, "The master key is not valid base64.", gen)]
    except OSError:
        return [Result(name, FAIL, "SECRETS_MASTER_KEY_FILE cannot be read.", "Check the path and its permissions.")]
    if len(key) != 32:
        return [Result(name, FAIL, f"{where} must decode to 32 bytes.", gen)]
    return _ok(name)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@check("Ports")
def ports(ctx: Context) -> list[Result]:
    out: list[Result] = []
    name = "Port"
    if _port_open(ctx.api_port):
        try:
            r = ctx.client().get(f"http://127.0.0.1:{ctx.api_port}/health")
            ours = r.status_code == 200
        except httpx.HTTPError:
            ours = False
        if not ours:
            out.append(
                Result(
                    f"{name} {ctx.api_port}",
                    FAIL,
                    f"Something other than Superaide is using port {ctx.api_port}, so the API cannot start.",
                    f"Find it with `lsof -i :{ctx.api_port}` and stop it, or start the API with `--port` set to another one.",
                )
            )
    front = urlsplit(ctx.settings.frontend_origin).port
    if front and front != ctx.api_port and _port_open(front):
        try:
            ctx.client().get(f"http://127.0.0.1:{front}/")
        except httpx.HTTPError:
            out.append(
                Result(
                    f"{name} {front}",
                    WARN,
                    f"Port {front} (the web app) is taken by something that does not answer HTTP.",
                    f"Find it with `lsof -i :{front}`.",
                )
            )
    return out or _ok("Ports free or serving Superaide")


@check("Telegram")
def telegram(ctx: Context) -> list[Result]:
    from superaide.identity.vault.check import CheckFailed, check_telegram

    name = "Telegram bot token"
    if not ctx.settings.telegram_bot_token:
        return [Result(name, OK, "Not set (Telegram is optional).")]
    try:
        check_telegram(ctx.settings.telegram_bot_token, ctx.client())
    except CheckFailed as exc:
        return [
            Result(
                name,
                FAIL,
                str(exc),
                "Copy the token again from @BotFather into TELEGRAM_BOT_TOKEN (or reconnect Telegram in the app).",
            )
        ]
    return _ok(name)


@check("Slack")
def slack(ctx: Context) -> list[Result]:
    from superaide.identity.vault.check import CheckFailed, check_slack

    name = "Slack tokens and Socket Mode"
    s = ctx.settings
    if not (s.slack_bot_token or s.slack_app_token):
        return [Result(name, OK, "Not set (Slack is optional).")]
    if not (s.slack_bot_token and s.slack_app_token):
        return [
            Result(
                name,
                FAIL,
                "Only one of SLACK_BOT_TOKEN and SLACK_APP_TOKEN is set.",
                "Set both (xoxb- and xapp-), or neither.",
            )
        ]
    try:
        check_slack(s.slack_bot_token, s.slack_app_token, ctx.client())
    except CheckFailed as exc:
        return [
            Result(
                name,
                FAIL,
                str(exc),
                "Fix SLACK_BOT_TOKEN / SLACK_APP_TOKEN, and turn Socket Mode on in the Slack app settings.",
            )
        ]
    return _ok(name)


def _origin(url: str) -> str:
    u = urlsplit(url.strip())
    return f"{u.scheme}://{u.netloc}".lower() if u.scheme and u.netloc else ""


@check("Google redirects")
def google_redirects(ctx: Context) -> list[Result]:
    from superaide import links

    name = "Google sign-in redirect addresses"
    s = ctx.settings
    if not (s.google_client_id or s.google_signin_enabled):
        return [Result(name, OK, "Google is not set up (optional).")]
    out: list[Result] = []
    extra = [o for o in (_origin(x) for x in s.google_redirect_origins.split(",")) if o]
    for origin in extra:
        if not links.origin_allowed(origin, s):
            out.append(
                Result(
                    name,
                    FAIL,
                    f"GOOGLE_REDIRECT_ORIGINS lists {origin}, which LINK_ORIGINS and DEV_ORIGINS do not allow, so sign-in from it is refused.",
                    f"Add {origin} to LINK_ORIGINS (or its host to DEV_ORIGINS).",
                )
            )
    base = links.public_base(s)
    if links.is_local(base) and (s.deploy_mode == "hosted" or s.session_cookie_secure) and not extra:
        out.append(
            Result(
                name,
                FAIL,
                "Google would send people back to localhost.",
                "Set PUBLIC_BASE_URL and FRONTEND_ORIGIN to your public address.",
            )
        )
    wanted = sorted({_origin(base), *extra} - {""})
    if not out:
        out.append(
            Result(
                name,
                WARN,
                "Superaide cannot see Google's console, so it cannot confirm these are registered.",
                "In Google Cloud, Credentials, your OAuth client, add these redirect URIs: "
                + ", ".join(f"{o}/api/auth/google/callback" for o in wanted),
            )
        )
    return out


def _local_but_public(s: Settings) -> bool:
    """Links in mail would point at this machine although the deployment looks reachable by others."""
    from superaide import links

    outside = (
        s.deploy_mode == "hosted"
        or s.session_cookie_secure
        or (s.public_base_url and not links.is_local(s.public_base_url))
    )
    return links.is_local(links.public_base(s)) and bool(outside)


@check("Public address")
def public_address(ctx: Context) -> list[Result]:
    from superaide import links

    name = "Public address (PUBLIC_BASE_URL)"
    s = ctx.settings
    if _local_but_public(s):
        return [
            Result(
                name,
                FAIL,
                f"Links in emails would point at {links.public_base(s)}, which other people cannot open, so the card link is left out.",
                "Set PUBLIC_BASE_URL (and FRONTEND_ORIGIN) to the address people reach Superaide at, e.g. https://ava.example.com, and restart.",
            )
        ]
    if not s.public_base_url:
        return [Result(name, OK, "Not set; fine for use on this machine only.")]
    try:
        r = ctx.client().get(s.public_base_url.rstrip("/") + "/health")
    except httpx.HTTPError:
        return [
            Result(
                name,
                FAIL,
                f"{_origin(s.public_base_url) or 'PUBLIC_BASE_URL'} cannot be reached from this machine.",
                "Check DNS, the proxy or tunnel, and that the API is running; then open the address in a browser.",
            )
        ]
    if r.status_code >= 500 or r.status_code == 404:
        return [
            Result(
                name,
                WARN,
                f"The public address answered {r.status_code} on /health.",
                "Route /health (and /api, /webhooks) from the public address to the API.",
            )
        ]
    return _ok(name)


@check("Spend cap")
def spend_cap(ctx: Context) -> list[Result]:
    name = "Spend cap (MAX_SPEND_USD)"
    cap = ctx.settings.max_spend_usd
    if cap <= 0:
        return [
            Result(
                name,
                FAIL,
                "The Claude spend cap is zero or negative, so every model call is refused.",
                "Set MAX_SPEND_USD to a dollar amount, e.g. MAX_SPEND_USD=10.",
            )
        ]
    if cap > 1000:
        return [
            Result(
                name,
                WARN,
                f"The spend cap is ${cap:,.0f}, which is very high.",
                "Set MAX_SPEND_USD to the most you are willing to pay.",
            )
        ]
    return _ok(name)


def run_all(ctx: Context) -> list[Result]:
    results: list[Result] = []
    for title, fn in CHECKS:
        try:
            results.extend(fn(ctx))
        except Exception as exc:  # a broken check must not hide the others
            results.append(
                Result(
                    title,
                    WARN,
                    f"This check could not run ({type(exc).__name__}).",
                    "Run again; report it if it persists.",
                )
            )
    return results


def render(results: list[Result]) -> str:
    tag = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}
    lines = []
    for r in results:
        lines.append(f"[{tag[r.status]}] {r.name}" + (f": {r.cause}" if r.cause else ""))
        if r.fix and r.status != OK:
            lines.append(f"        Fix: {r.fix}")
    fails = sum(r.status == FAIL for r in results)
    warns = sum(r.status == WARN for r in results)
    lines.append("")
    lines.append("Everything looks fine." if not fails and not warns else f"{fails} to fix, {warns} to look at.")
    return "\n".join(lines)


def run(env_path: Path | None = None, port: int = 8000) -> int:
    """Print the report; the exit code is 1 when anything failed."""
    ctx = Context(settings=get_settings(), env_path=env_path or Path(".env"), api_port=port)
    results = run_all(ctx)
    print(render(results))
    return 1 if any(r.status == FAIL for r in results) else 0
