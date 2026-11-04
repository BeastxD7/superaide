"""`superaide doctor` (#409): each check reports the failure it is built for with a cause and an exact fix, and
no output ever contains a secret. Fake HTTP only; nothing leaves the machine."""

from __future__ import annotations

import base64

import httpx

from superaide import doctor
from superaide.platform.config import Settings

SECRETS = {
    "telegram_bot_token": "123456789" + ":AAE" + "secret" * 5 + "12",
    "slack_bot_token": "xoxb-secret-bot-token",
    "slack_app_token": "xapp-secret-app-token",
    "anthropic_api_key": "sk-ant-secret-key-value",
    "admin_token": "admin-secret-value",
}


def settings(**kw) -> Settings:
    base = {"public_base_url": "", "frontend_origin": "http://localhost:3000", "session_cookie_secure": False}
    return Settings(_env_file=None, **{**base, **kw})


def fake(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def ctx(tmp_path, http=None, **kw) -> doctor.Context:
    example = tmp_path / ".env.example"
    example.write_text("ADMIN_TOKEN=\n# MAX_SPEND_USD=10\n")
    return doctor.Context(
        settings=kw.pop("s", settings()), env_path=tmp_path / ".env", example_path=example, http=http,
        environ=kw.pop("environ", {}), api_port=kw.pop("api_port", 59999),
    )  # fmt: skip


def one(fn, c) -> doctor.Result:
    results = fn(c)
    return max(results, key=lambda r: [doctor.OK, doctor.WARN, doctor.FAIL].index(r.status))


def test_env_file_duplicate_key_typo_and_bad_line(tmp_path):
    c = ctx(tmp_path)
    (tmp_path / ".env").write_text("ADMIN_TOKEN=a\nADMIN_TOKEN=b\nMAX_SPEND_USDD=5\nnot a setting line\n")
    results = doctor.env_file(c)
    text = " ".join(r.cause for r in results)
    assert any(r.status == doctor.FAIL for r in results)
    assert "ADMIN_TOKEN appears 2 times" in text and "Did you mean MAX_SPEND_USD" in text and "Line 4" in text


def test_env_file_missing_and_invalid_value_never_echoes_it(tmp_path):
    c = ctx(tmp_path)
    assert one(doctor.env_file, c).status == doctor.WARN
    (tmp_path / ".env").write_text("PRINCIPAL_CARD_DAYS=hunter2-not-a-number\n")
    r = one(doctor.env_file, c)
    assert r.status == doctor.FAIL and "PRINCIPAL_CARD_DAYS" in r.cause and "hunter2" not in r.cause + r.fix


def test_env_file_fine(tmp_path):
    (tmp_path / ".env").write_text("ADMIN_TOKEN=abc\nMAX_SPEND_USD=5\n")
    assert one(doctor.env_file, ctx(tmp_path)).status == doctor.OK


def test_database_unreachable_fails_with_the_command(tmp_path):
    bad = settings(database_url="postgresql+psycopg://x:y@127.0.0.1:1/none")
    r = one(doctor.database, ctx(tmp_path, s=bad))
    assert r.status == doctor.FAIL and "docker compose up -d" in r.fix and "x:y" not in r.cause + r.fix


def test_migrations_behind_and_multiple_heads(tmp_path, monkeypatch):
    from superaide.platform.security import dbcheck

    for state, fragment in (("behind", "alembic upgrade head"), ("multiple_heads", "alembic heads")):
        monkeypatch.setattr(dbcheck, "status", lambda state=state: dbcheck.DbStatus(state, ("a",), ("b",)))
        r = one(doctor.migrations, ctx(tmp_path))
        assert r.status == doctor.FAIL and fragment in r.fix
    monkeypatch.setattr(dbcheck, "status", lambda: dbcheck.DbStatus("ok", ("a",), ("a",)))
    assert one(doctor.migrations, ctx(tmp_path)).status == doctor.OK


def test_pgvector_missing(tmp_path, monkeypatch):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def scalar(self, *a, **k):
            return 0

    class Engine:
        def connect(self):
            return Conn()

    monkeypatch.setattr("superaide.platform.db.session.get_engine", lambda url: Engine())
    r = one(doctor.pgvector, ctx(tmp_path))
    assert r.status == doctor.FAIL and "CREATE EXTENSION vector" in r.fix


def test_master_key_checks(tmp_path):
    good = base64.b64encode(b"k" * 32).decode()
    assert one(doctor.master_key, ctx(tmp_path, environ={"SECRETS_MASTER_KEY": good})).status == doctor.OK
    r = one(doctor.master_key, ctx(tmp_path, environ={"SECRETS_MASTER_KEY": "!!notbase64!!"}))
    assert r.status == doctor.FAIL and "openssl rand -base64 32" in r.fix and "notbase64" not in r.cause
    short = base64.b64encode(b"k" * 8).decode()
    assert one(doctor.master_key, ctx(tmp_path, environ={"SECRETS_MASTER_KEY": short})).status == doctor.FAIL
    r = one(doctor.master_key, ctx(tmp_path, environ={"SECRETS_MASTER_KEY_FILE": str(tmp_path / "nope")}))
    assert r.status == doctor.FAIL


def test_a_port_held_by_something_else_fails_with_lsof(tmp_path):
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        r = one(doctor.ports, ctx(tmp_path, http=fake(lambda req: httpx.Response(404)), api_port=port))
        assert r.status == doctor.FAIL and f"lsof -i :{port}" in r.fix
        r = one(doctor.ports, ctx(tmp_path, http=fake(lambda req: httpx.Response(200, json={})), api_port=port))
        assert r.status == doctor.OK
    finally:
        srv.close()


def test_telegram_token_rejected(tmp_path):
    s = settings(telegram_bot_token=SECRETS["telegram_bot_token"])
    bad = fake(lambda req: httpx.Response(401, json={"ok": False}))
    r = one(doctor.telegram, ctx(tmp_path, http=bad, s=s))
    assert r.status == doctor.FAIL and "BotFather" in r.fix
    good = fake(lambda req: httpx.Response(200, json={"ok": True, "result": {"username": "ava_bot"}}))
    assert one(doctor.telegram, ctx(tmp_path, http=good, s=s)).status == doctor.OK
    assert one(doctor.telegram, ctx(tmp_path)).status == doctor.OK  # not set: optional


def test_slack_tokens_and_socket_mode(tmp_path):
    s = settings(slack_bot_token="xoxb-1", slack_app_token="xapp-1")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True, "team_id": "T1"})
        return httpx.Response(200, json={"ok": False})  # Socket Mode off

    r = one(doctor.slack, ctx(tmp_path, http=fake(handler), s=s))
    assert r.status == doctor.FAIL and "Socket Mode" in r.fix
    half = settings(slack_bot_token="xoxb-1")
    assert one(doctor.slack, ctx(tmp_path, s=half)).status == doctor.FAIL


def test_google_redirect_origin_not_allowed_and_localhost_public(tmp_path):
    s = settings(google_client_id="id", google_redirect_origins="https://other.example.test")
    r = one(doctor.google_redirects, ctx(tmp_path, s=s))
    assert r.status == doctor.FAIL and "LINK_ORIGINS" in r.fix
    ok = settings(
        google_client_id="id", google_redirect_origins="https://a.example.test", link_origins="https://a.example.test"
    )
    r = one(doctor.google_redirects, ctx(tmp_path, s=ok))
    assert r.status == doctor.WARN and "https://a.example.test/api/auth/google/callback" in r.fix
    hosted = settings(google_client_id="id", deploy_mode="hosted")
    assert one(doctor.google_redirects, ctx(tmp_path, s=hosted)).status == doctor.FAIL


def test_public_address_local_unreachable_and_fine(tmp_path):
    hosted = settings(deploy_mode="hosted")
    r = one(doctor.public_address, ctx(tmp_path, s=hosted))
    assert r.status == doctor.FAIL and "PUBLIC_BASE_URL" in r.fix
    s = settings(public_base_url="https://ava.example.test")

    def down(req):
        raise httpx.ConnectError("no")

    assert one(doctor.public_address, ctx(tmp_path, s=s, http=fake(down))).status == doctor.FAIL
    assert one(doctor.public_address, ctx(tmp_path, s=s, http=fake(lambda r: httpx.Response(200)))).status == doctor.OK


def test_spend_cap(tmp_path):
    assert one(doctor.spend_cap, ctx(tmp_path, s=settings(max_spend_usd=0))).status == doctor.FAIL
    assert one(doctor.spend_cap, ctx(tmp_path, s=settings(max_spend_usd=5))).status == doctor.OK


def test_the_report_never_contains_a_secret(tmp_path, capsys):
    s = settings(**SECRETS, google_client_id="gid", google_client_secret="google-secret-value", max_spend_usd=0)
    (tmp_path / ".env").write_text("".join(f"{k.upper()}={v}\n" for k, v in SECRETS.items()) + "ADMIN_TOKEN=dup\n")
    bad = fake(lambda req: httpx.Response(401, json={"ok": False}))
    c = ctx(tmp_path, http=bad, s=s)
    results = doctor.run_all(c)
    report = doctor.render(results)
    assert any(r.status == doctor.FAIL for r in results)
    for secret in [*SECRETS.values(), "google-secret-value"]:
        assert secret not in report


def test_cli_exit_code_is_one_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "run_all", lambda c: [doctor.Result("x", doctor.FAIL, "bad", "do this")])
    assert doctor.run(tmp_path / ".env") == 1
    monkeypatch.setattr(doctor, "run_all", lambda c: [doctor.Result("x", doctor.OK)])
    assert doctor.run(tmp_path / ".env") == 0
