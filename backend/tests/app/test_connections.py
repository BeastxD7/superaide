"""Connections: credentials are checked, encrypted, masked, owner-only and applied."""

from __future__ import annotations

import base64
import json
import os
import stat
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from superaide.app import connections
from superaide.main import create_app
from superaide.platform.db.models import AuditLog, IntegrationSecret
from superaide.platform.db.session import session_scope
from superaide.vault import crypto
from superaide.vault.store import runtime_settings

PASSWORD = "correct horse battery"
CLAUDE_KEY = "sk-ant-api03-" + "x" * 80 + "4f2a"
TG_TOKEN = "123456789:" + "A" * 35
GOOGLE_CLIENT = "1234-abc.apps.googleusercontent.com"
NO_SCOPE_CLIENT = "5678-def.apps.googleusercontent.com"
GOOGLE_SECRET = "test-google-client-secret-9z"
GOOGLE_REFRESH = "1//test-refresh-token-7q"
TOKEN_FORMS: list[dict[str, list[str]]] = []  # what each token exchange sent to (the fake) Google


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)  # a server's setting must not leak into tests
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


def services(request: httpx.Request) -> httpx.Response:
    """Pretend Anthropic, Telegram and Slack."""
    url = str(request.url)
    if "api.anthropic.com" in url:
        ok = request.headers.get("x-api-key") == CLAUDE_KEY
        return httpx.Response(200 if ok else 401, json={"data": []})
    if "api.telegram.org" in url:
        ok = TG_TOKEN in url
        return httpx.Response(200, json={"ok": ok, "result": {"username": "ava_for_jordan_bot", "first_name": "Ava"}})
    if "auth.test" in url:
        return httpx.Response(200, json={"ok": True, "team_id": "T123", "team": "Larkfield", "user": "ava"})
    if "apps.connections.open" in url:
        return httpx.Response(200, json={"ok": True, "url": "wss://example"})
    if "oauth2.googleapis.com/token" in url:
        form = parse_qs(request.content.decode())
        TOKEN_FORMS.append(form)
        if form.get("code") != ["good-code"] or not form.get("code_verifier"):
            return httpx.Response(400, json={"error": "invalid_grant"})
        scope = (
            "" if form.get("client_id") == [NO_SCOPE_CLIENT] else "https://www.googleapis.com/auth/calendar.readonly"
        )
        return httpx.Response(200, json={"access_token": "ya29.x", "refresh_token": GOOGLE_REFRESH, "scope": scope})
    return httpx.Response(404)


@pytest.fixture
def owner(agent, monkeypatch):
    TOKEN_FORMS.clear()
    monkeypatch.setattr(connections, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(services)))
    app = create_app(agent)
    c = TestClient(app, headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": PASSWORD})
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    c.app_ = app
    return c


def test_secrets_are_encrypted_bound_to_their_slot_and_masked():
    token = crypto.encrypt(CLAUDE_KEY, "anthropic/api_key")
    assert CLAUDE_KEY not in token and crypto.decrypt(token, "anthropic/api_key") == CLAUDE_KEY
    with pytest.raises(crypto.VaultError):
        crypto.decrypt(token, "telegram/bot_token")  # can't be moved to another slot
    assert crypto.mask(CLAUDE_KEY) == "sk-…4f2a" and CLAUDE_KEY[8:-4] not in crypto.mask(CLAUDE_KEY)


def test_key_file_fallback_is_private(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRETS_MASTER_KEY")
    monkeypatch.setattr(crypto, "_from_keyring", lambda: None)
    path = tmp_path / "master.key"
    monkeypatch.setenv("SUPERAIDE_KEY_FILE", str(path))
    crypto.master_key.cache_clear()
    key, where = crypto.master_key()
    assert where == "key file" and len(key) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    os.chmod(path, 0o644)
    crypto.master_key.cache_clear()
    with pytest.raises(crypto.VaultError):
        crypto.master_key()


def test_connecting_claude_goes_through_the_ai_model_api_and_never_echoes_the_key(owner):
    bad = owner.post("/app/providers/anthropic", json={"api_key": "sk-ant-wrong-key-123456"})
    assert bad.status_code == 400 and "didn't accept" in bad.json()["detail"]
    r = owner.post("/app/providers/anthropic", json={"api_key": CLAUDE_KEY})
    assert r.status_code == 200, r.text
    assert r.json()["claude"]["connected"] is True
    assert CLAUDE_KEY not in r.text and CLAUDE_KEY not in owner.get("/app/providers").text
    # Connections lists channels and integrations only: no Claude card, no Claude route.
    status = owner.get("/app/connections")
    assert "anthropic" not in status.json()["services"] and CLAUDE_KEY not in status.text
    assert owner.post("/app/connections/anthropic", json={"api_key": CLAUDE_KEY}).status_code in (404, 405)
    with session_scope() as s:
        row = s.scalar(select(IntegrationSecret))
        assert CLAUDE_KEY not in row.ciphertext
        assert all(CLAUDE_KEY not in json.dumps(a.detail) for a in s.scalars(select(AuditLog)))
    # The agent's settings now carry the key (applied without a restart).
    assert runtime_settings().anthropic_api_key == CLAUDE_KEY


def test_telegram_and_slack_connect_and_disconnect(owner):
    assert owner.post("/app/connections/telegram", json={"bot_token": "not-a-token-at-all"}).status_code == 400
    r = owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN}).json()
    telegram = r["services"]["telegram"]
    assert telegram["connected"] and telegram["meta"]["username"] == "ava_for_jordan_bot"
    slack = {"bot_token": "xoxb-1-abc-defghijk", "app_token": "xapp-1-abc-defghi"}
    r = owner.post("/app/connections/slack", json=slack)
    assert r.json()["services"]["slack"]["connected"]
    assert runtime_settings().slack_team_id == "T123"
    assert "manifest_yaml=" in r.json()["guides"]["slack"]["url"]
    r = owner.post("/app/connections/telegram/disconnect").json()
    assert r["services"]["telegram"]["connected"] is False
    assert r["services"]["whatsapp"]["available"] is False


def test_a_rejected_telegram_token_shows_on_connections_until_a_new_one_is_saved(owner):
    from superaide.channels import health

    assert owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN}).status_code == 200
    assert owner.get("/app/connections").json()["services"]["telegram"]["health"]["status"] == "ok"
    health.record("telegram", health.AUTH_FAILED, "Telegram rejected the bot token (401)")  # e.g. from a send
    tg = owner.get("/app/connections").json()["services"]["telegram"]
    assert tg["connected"] is True
    assert tg["health"]["status"] == "auth_failed" and tg["health"]["since"]
    assert "rejected" in tg["health"]["last_error"] and TG_TOKEN not in json.dumps(tg)
    # Reconnecting with a token Telegram accepts clears it.
    r = owner.post("/app/connections/telegram", json={"bot_token": TG_TOKEN}).json()
    assert r["services"]["telegram"]["health"]["status"] == "ok"


def test_me_carries_each_channels_health_without_asking_the_service(owner, monkeypatch):
    from superaide.app import routes
    from superaide.channels import health

    def no_network(*args, **kwargs):
        raise AssertionError("GET /app/me must not call Telegram or Slack")

    monkeypatch.setattr(httpx, "get", no_network)
    monkeypatch.setattr(httpx, "post", no_network)
    routes.telegram_username.cache_clear()
    h = owner.get("/app/me").json()["channels"]["health"]
    assert h["telegram"]["status"] == "ok" and h["slack"]["status"] == "ok"  # nothing has failed
    health.record("telegram", health.AUTH_FAILED, "Telegram rejected the bot token (401)")
    h = owner.get("/app/me").json()["channels"]["health"]
    assert h["telegram"]["status"] == "auth_failed" and h["telegram"]["since"]
    assert h["slack"]["status"] == "ok"  # one channel's verdict is not the other's


def test_only_the_owner_can_see_or_change_connections(owner, agent):
    other = TestClient(owner.app_, headers={"X-Requested-With": "superaide"})
    assert other.get("/app/connections").status_code == 401
    other.post("/auth/signup", json={"name": "Priya Tan", "email": "p@example.com", "password": PASSWORD})
    assert other.get("/app/connections").status_code == 403
    assert other.post("/app/providers/anthropic", json={"api_key": CLAUDE_KEY}).status_code == 403


def callback(client: TestClient, **params: str) -> httpx.Response:
    return client.get("/app/connections/google/callback", params=params, follow_redirects=False)


def google_start(client: TestClient) -> dict[str, str]:
    r = client.post("/app/connections/google/start")
    assert r.status_code == 200, r.text
    query = parse_qs(urlparse(r.json()["url"]).query)
    return {k: v[0] for k, v in query.items()}


def test_google_client_is_saved_masked_and_the_redirect_uri_is_shown(owner):
    first = owner.get("/app/connections").json()
    assert first["guides"]["google"]["redirect_uri"] == "http://localhost:3000/api/app/connections/google/callback"
    assert first["services"]["google"] | {"since": None} == {
        "connected": False, "via": None, "hints": {}, "meta": {}, "since": None, "client_ready": False,
    }  # fmt: skip
    assert owner.post("/app/connections/google/start").status_code == 409  # no client yet
    bad = owner.post("/app/connections/google", json={"client_id": "not-a-google-id", "client_secret": GOOGLE_SECRET})
    assert bad.status_code == 400 and "apps.googleusercontent.com" in bad.json()["detail"]

    r = owner.post("/app/connections/google", json={"client_id": GOOGLE_CLIENT, "client_secret": GOOGLE_SECRET})
    assert r.status_code == 200, r.text
    google = r.json()["services"]["google"]
    assert google["client_ready"] and not google["connected"]
    assert GOOGLE_SECRET not in r.text and GOOGLE_SECRET not in owner.get("/app/connections").text

    q = google_start(owner)
    assert q["client_id"] == GOOGLE_CLIENT and q["code_challenge_method"] == "S256" and q["code_challenge"]
    assert q["redirect_uri"] == first["guides"]["google"]["redirect_uri"]
    assert q["scope"] == "https://www.googleapis.com/auth/calendar.readonly" and q["access_type"] == "offline"


def test_google_sign_in_round_trip_stores_the_refresh_token_once(owner):
    owner.post("/app/connections/google", json={"client_id": GOOGLE_CLIENT, "client_secret": GOOGLE_SECRET})
    q = google_start(owner)
    # A forged or stale state is refused, and doesn't spoil the real sign-in.
    r = callback(owner, code="good-code", state="forged")
    assert r.status_code == 303 and r.headers["location"].endswith("/dashboard/capabilities?google=expired")
    r = callback(owner, code="good-code", state=q["state"])
    assert (
        r.status_code == 303
        and r.headers["location"] == "http://localhost:3000/dashboard/capabilities?google=connected"
    )
    google = owner.get("/app/connections").json()["services"]["google"]
    assert google["connected"] and google["via"] == "app"
    assert GOOGLE_REFRESH not in json.dumps(google) and GOOGLE_SECRET not in json.dumps(google)
    assert runtime_settings().google_refresh_token == GOOGLE_REFRESH
    # The state works once.
    again = callback(owner, code="good-code", state=q["state"])
    assert again.headers["location"].endswith("?google=expired")
    # A new client needs a new sign-in: the old refresh token is dropped.
    r = owner.post("/app/connections/google", json={"client_id": NO_SCOPE_CLIENT, "client_secret": GOOGLE_SECRET})
    assert r.json()["services"]["google"]["connected"] is False and runtime_settings().google_refresh_token == ""
    owner.post("/app/connections/google", json={"client_id": GOOGLE_CLIENT, "client_secret": GOOGLE_SECRET})
    google_start(owner)
    r = owner.post("/app/connections/google/disconnect").json()
    assert r["services"]["google"] | {"since": None} == {
        "connected": False, "via": None, "hints": {}, "meta": {}, "since": None, "client_ready": False,
    }  # fmt: skip


def test_google_callback_sends_every_outcome_back_to_connections(owner, agent):
    def outcome(client: TestClient, **params: str) -> str:
        r = callback(client, **params)
        assert r.status_code == 303
        return r.headers["location"].rsplit("?google=", 1)[1]

    owner.post("/app/connections/google", json={"client_id": GOOGLE_CLIENT, "client_secret": GOOGLE_SECRET})
    q = google_start(owner)
    assert outcome(owner, error="access_denied", state=q["state"]) == "cancelled"
    q = google_start(owner)
    assert outcome(owner, code="bad-code", state=q["state"]) == "failed"
    owner.post("/app/connections/google", json={"client_id": NO_SCOPE_CLIENT, "client_secret": GOOGLE_SECRET})
    q = google_start(owner)
    assert outcome(owner, code="good-code", state=q["state"]) == "no_calendar"
    assert runtime_settings().google_refresh_token == ""
    # A browser whose session lapsed still lands back on Connections with a message, not a bare 401.
    signed_out = TestClient(owner.app_)
    assert outcome(signed_out, code="good-code", state="whatever") == "expired"
    assert outcome(signed_out) == "cancelled"


def test_only_the_owner_can_set_up_google(owner):
    other = TestClient(owner.app_, headers={"X-Requested-With": "superaide"})
    other.post("/auth/signup", json={"name": "Priya Tan", "email": "p@example.com", "password": PASSWORD})
    body = {"client_id": GOOGLE_CLIENT, "client_secret": GOOGLE_SECRET}
    assert other.post("/app/connections/google", json=body).status_code == 403
    owner.post("/app/connections/google", json=body)
    assert other.post("/app/connections/google/start").status_code == 403
    assert other.post("/app/connections/google/disconnect").status_code == 403


def test_the_deployments_google_client_serves_every_owners_calendar_even_when_hosted(owner, monkeypatch):
    """One OAuth client for sign-in and Calendar: set in .env, an owner connects without pasting one.
    The refresh token is still each owner's own, and a hosted server never uses one from .env."""
    from superaide.platform.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "deploy_mode", "hosted")
    monkeypatch.setattr(settings, "google_client_id", GOOGLE_CLIENT)
    monkeypatch.setattr(settings, "google_client_secret", GOOGLE_SECRET)
    monkeypatch.setattr(settings, "google_refresh_token", "1//the-servers-own-token")
    google = owner.get("/app/connections").json()["services"]["google"]
    assert google["client_ready"] and not google["connected"]
    assert runtime_settings().google_refresh_token == ""  # never the server's token for an owner
    q = google_start(owner)
    assert q["client_id"] == GOOGLE_CLIENT
    assert callback(owner, code="good-code", state=q["state"]).headers["location"].endswith("?google=connected")
    google = owner.get("/app/connections").json()["services"]["google"]
    assert google["connected"] and google["via"] == "app" and GOOGLE_SECRET not in json.dumps(google)
    assert runtime_settings().google_refresh_token == GOOGLE_REFRESH


# --- connecting from the address the owner is using (not always FRONTEND_ORIGIN) ---------------------

PUBLIC = "https://superaide.webgraph.fun"
HOME = "http://localhost:3000"


def public_setup(owner, monkeypatch, *, registered: bool = True) -> None:
    from superaide.platform.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "link_origins", PUBLIC)  # an address we serve (links.py)
    monkeypatch.setattr(settings, "google_redirect_origins", PUBLIC if registered else "")
    owner.post("/app/connections/google", json={"client_id": GOOGLE_CLIENT, "client_secret": GOOGLE_SECRET})


def start_on(owner, headers: dict[str, str]) -> httpx.Response:
    return owner.post("/app/connections/google/start", headers=headers)


def test_connect_on_the_public_address_uses_it_for_the_authorise_url_the_token_exchange_and_the_way_back(
    owner, monkeypatch
):
    public_setup(owner, monkeypatch)
    r = start_on(owner, {"Origin": PUBLIC})
    assert r.status_code == 200, r.text
    q = {k: v[0] for k, v in parse_qs(urlparse(r.json()["url"]).query).items()}
    assert q["redirect_uri"] == f"{PUBLIC}/api/app/connections/google/callback"
    done = callback(owner, code="good-code", state=q["state"])
    assert done.headers["location"] == f"{PUBLIC}/dashboard/capabilities?google=connected"  # not localhost
    assert TOKEN_FORMS[-1]["redirect_uri"] == [q["redirect_uri"]]  # Google needs the same address twice
    # The forwarded host works too, as behind a tunnel or proxy.
    r = start_on(owner, {"X-Forwarded-Host": "superaide.webgraph.fun", "X-Forwarded-Proto": "https"})
    assert parse_qs(urlparse(r.json()["url"]).query)["redirect_uri"] == [
        f"{PUBLIC}/api/app/connections/google/callback"
    ]


def test_the_guide_lists_the_addresses_for_where_the_owner_is_and_the_configured_ones(owner, monkeypatch):
    public_setup(owner, monkeypatch)
    g = owner.get("/app/connections", headers={"Origin": PUBLIC}).json()["guides"]["google"]
    assert g["redirect_uri"] == f"{PUBLIC}/api/app/connections/google/callback"
    assert g["signin_redirect_uri"] == f"{PUBLIC}/api/auth/google/callback"
    assert g["redirect_uris"] == [f"{PUBLIC}{p}" for p in ("/api/app/connections/google/callback",)] + [
        f"{HOME}/api/app/connections/google/callback"
    ]
    assert f"{HOME}/api/auth/google/callback" in g["signin_redirect_uris"]
    # Unchanged on the normal address.
    g = owner.get("/app/connections").json()["guides"]["google"]
    assert g["redirect_uri"] == f"{HOME}/api/app/connections/google/callback"


def test_an_allowed_address_that_google_does_not_know_gets_a_message_not_a_failed_google_page(owner, monkeypatch):
    public_setup(owner, monkeypatch, registered=False)
    r = start_on(owner, {"Origin": PUBLIC})
    detail = r.json()["detail"]
    assert r.status_code == 409 and "registered in Google Cloud" in detail
    assert f"{PUBLIC}/api/app/connections/google/callback" in detail and f"{PUBLIC}/api/auth/google/callback" in detail
    assert f"open the app at {HOME}" in detail
    assert "google_pending" not in str(owner.get("/app/connections").json())  # nothing was started
    with session_scope() as s:
        from superaide.platform.tenancy.models import PlatformState

        assert s.scalars(select(PlatformState).where(PlatformState.key.like("google_pending:%"))).all() == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": f"{PUBLIC}.evil.example"},
        {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"},
        {"Origin": "javascript://superaide.webgraph.fun"},
    ],
)
def test_a_spoofed_origin_is_never_reflected(owner, monkeypatch, headers):
    public_setup(owner, monkeypatch)
    from superaide.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "google_redirect_origins", f"{PUBLIC},https://evil.example")  # even if listed
    r = start_on(owner, headers)
    assert r.status_code == 200
    q = {k: v[0] for k, v in parse_qs(urlparse(r.json()["url"]).query).items()}
    assert q["redirect_uri"] == f"{HOME}/api/app/connections/google/callback"
    done = callback(owner, code="good-code", state=q["state"])
    assert done.headers["location"].startswith(f"{HOME}/") and "evil" not in done.headers["location"]


def test_the_stored_address_is_checked_again_at_the_callback(owner, monkeypatch):
    from superaide.platform.tenancy.models import PlatformState

    public_setup(owner, monkeypatch)
    q = {k: v[0] for k, v in parse_qs(urlparse(start_on(owner, {"Origin": PUBLIC}).json()["url"]).query).items()}
    # Un-registered meanwhile: the way back and the token request fall back to the normal address.
    monkeypatch.setattr(get_settings_(), "google_redirect_origins", "")
    done = callback(owner, code="good-code", state=q["state"])
    assert done.headers["location"].startswith(f"{HOME}/") and PUBLIC not in done.headers["location"]
    # A tampered record (someone with database access, or a bug) cannot send the browser anywhere else.
    monkeypatch.setattr(get_settings_(), "google_redirect_origins", PUBLIC)
    q = {k: v[0] for k, v in parse_qs(urlparse(start_on(owner, {"Origin": PUBLIC}).json()["url"]).query).items()}
    with session_scope() as s:
        row = s.get(PlatformState, connections._pending_key(q["state"]))
        row.value = {**row.value, "origin": "https://evil.example"}
    done = callback(owner, code="good-code", state=q["state"])
    assert done.headers["location"].startswith(f"{HOME}/") and "evil" not in done.headers["location"]
    assert TOKEN_FORMS[-1]["redirect_uri"] == [f"{HOME}/api/app/connections/google/callback"]


def get_settings_():
    from superaide.platform.config import get_settings

    return get_settings()


def test_the_default_localhost_flow_is_unchanged(owner):
    owner.post("/app/connections/google", json={"client_id": GOOGLE_CLIENT, "client_secret": GOOGLE_SECRET})
    for headers in ({}, {"Origin": HOME}):
        q = {k: v[0] for k, v in parse_qs(urlparse(start_on(owner, headers).json()["url"]).query).items()}
        assert q["redirect_uri"] == f"{HOME}/api/app/connections/google/callback"
    done = callback(owner, code="good-code", state=q["state"])
    assert done.headers["location"] == f"{HOME}/dashboard/capabilities?google=connected"
    assert TOKEN_FORMS[-1]["redirect_uri"] == [f"{HOME}/api/app/connections/google/callback"]
