"""Credential checks against pretend services, and the vault's encryption edge cases.

Every check runs against httpx.MockTransport: nothing here touches the network
or the real keychain.
"""

from __future__ import annotations

import base64
import os
import stat

import httpx
import pytest

from superaide.vault import crypto
from superaide.vault.check import CheckFailed, check_anthropic, check_slack, check_telegram

CLAUDE_KEY = "sk-ant-api03-" + "x" * 80 + "4f2a"
TG_TOKEN = "123456789:" + "A" * 35
SLACK_BOT = "xoxb-1-abc-defghijk"
SLACK_APP = "xapp-1-abc-defghi"


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)  # a server's setting must not leak into tests
    crypto.master_key.cache_clear()
    yield
    crypto.master_key.cache_clear()


def client_for(handler) -> tuple[httpx.Client, list[httpx.Request]]:
    """A client whose every request goes to `handler`; the requests are kept for inspection."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record)), seen


def respond(status: int = 200, **kwargs):
    return lambda request: httpx.Response(status, **kwargs)


def raise_(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def slack_service(auth: dict | None = None, conn: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth.test" in str(request.url):
            return httpx.Response(200, json=auth or {"ok": True, "team_id": "T123", "team": "Larkfield", "user": "ava"})
        return httpx.Response(200, json=conn or {"ok": True, "url": "wss://example"})

    return handler


def assert_no_secret(exc: pytest.ExceptionInfo, *secrets: str) -> None:
    for secret in secrets:
        assert secret.strip() not in str(exc.value)


# --- Claude ---------------------------------------------------------------------------


def test_claude_key_is_checked_with_one_read_only_call():
    http, seen = client_for(respond(200, json={"data": [{"id": "claude-haiku-5-5"}]}))
    checked = check_anthropic(CLAUDE_KEY, http)
    assert checked.values == {"api_key": CLAUDE_KEY} and checked.meta == {}
    (request,) = seen
    assert request.method == "GET" and request.url.path == "/v1/models"
    assert request.headers["x-api-key"] == CLAUDE_KEY and request.headers["anthropic-version"]


def test_claude_key_with_surrounding_whitespace_is_trimmed_before_use():
    http, seen = client_for(respond(200, json={"data": []}))
    checked = check_anthropic(f"  {CLAUDE_KEY}\n\t", http)
    assert checked.values["api_key"] == CLAUDE_KEY
    assert seen[0].headers["x-api-key"] == CLAUDE_KEY


def test_claude_key_that_isnt_one_is_refused_without_a_call():
    http, seen = client_for(respond(200))
    with pytest.raises(CheckFailed, match="start with sk-ant-"):
        check_anthropic("sk-proj-not-anthropic", http)
    with pytest.raises(CheckFailed, match="start with sk-ant-"):
        check_anthropic("   ", http)
    assert seen == []


@pytest.mark.parametrize("status", [401, 403])
def test_claude_rejects_a_wrong_key(status):
    http, _ = client_for(respond(status, json={"type": "error", "error": {"type": "authentication_error"}}))
    with pytest.raises(CheckFailed, match="didn't accept that key") as exc:
        check_anthropic(CLAUDE_KEY, http)
    assert_no_secret(exc, CLAUDE_KEY)


@pytest.mark.parametrize("status", [429, 500, 529])
def test_claude_service_errors_say_try_again(status):
    http, _ = client_for(respond(status, text="overloaded"))
    with pytest.raises(CheckFailed, match=rf"error \({status}\)\. Try again") as exc:
        check_anthropic(CLAUDE_KEY, http)
    assert_no_secret(exc, CLAUDE_KEY)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("no route to host"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.ReadTimeout("read timed out"),
    ],
)
def test_claude_unreachable_or_slow(exc):
    http, _ = client_for(raise_(exc))
    with pytest.raises(CheckFailed, match="Couldn't reach Anthropic") as info:
        check_anthropic(CLAUDE_KEY, http)
    assert_no_secret(info, CLAUDE_KEY)


# --- Telegram -------------------------------------------------------------------------


def test_telegram_token_is_checked_and_the_bot_named():
    http, seen = client_for(respond(200, json={"ok": True, "result": {"username": "ava_bot", "first_name": "Ava"}}))
    checked = check_telegram(TG_TOKEN, http)
    assert checked.values == {"bot_token": TG_TOKEN}
    assert checked.meta == {"username": "ava_bot", "name": "Ava"}
    assert seen[0].url.path == f"/bot{TG_TOKEN}/getMe"


def test_telegram_token_with_surrounding_whitespace_is_trimmed_before_use():
    http, seen = client_for(respond(200, json={"ok": True, "result": {}}))
    checked = check_telegram(f"\n {TG_TOKEN} \n", http)
    assert checked.values["bot_token"] == TG_TOKEN and checked.meta == {"username": None, "name": None}
    assert seen[0].url.path == f"/bot{TG_TOKEN}/getMe"


@pytest.mark.parametrize("token", ["", "not-a-token", "1234:" + "A" * 35, "123456789:" + "A" * 10, "123456789 AAA"])
def test_telegram_token_that_isnt_one_is_refused_without_a_call(token):
    http, seen = client_for(respond(200))
    with pytest.raises(CheckFailed, match="doesn't look like a bot token"):
        check_telegram(token, http)
    assert seen == []


@pytest.mark.parametrize("status", [401, 403, 404])
def test_telegram_rejects_a_wrong_token(status):
    body = {"ok": False, "error_code": status, "description": "Unauthorized"}
    http, _ = client_for(respond(status, json=body))
    with pytest.raises(CheckFailed, match="didn't accept that token") as exc:
        check_telegram(TG_TOKEN, http)
    assert_no_secret(exc, TG_TOKEN)


@pytest.mark.parametrize(
    "handler",
    [
        raise_(httpx.ConnectError("dns failure")),
        raise_(httpx.ReadTimeout("read timed out")),
        respond(502, text="<html>Bad gateway</html>"),  # a proxy's error page, not JSON
        respond(200, text="not json"),
    ],
    ids=["down", "timeout", "html-error-page", "not-json"],
)
def test_telegram_unreachable_or_garbled(handler):
    http, _ = client_for(handler)
    with pytest.raises(CheckFailed, match="Couldn't reach Telegram") as exc:
        check_telegram(TG_TOKEN, http)
    assert_no_secret(exc, TG_TOKEN)


def test_telegram_ok_without_a_true_flag_is_not_accepted():
    http, _ = client_for(respond(200, json={"result": {"username": "ava_bot"}}))
    with pytest.raises(CheckFailed, match="didn't accept"):
        check_telegram(TG_TOKEN, http)


@pytest.mark.parametrize("body", [b"null", b"[]", b'{"ok": true, "result": null}'], ids=["null", "list", "null-result"])
def test_telegram_json_that_isnt_an_object_is_a_plain_failure(body):
    http, _ = client_for(respond(200, content=body, headers={"Content-Type": "application/json"}))
    with pytest.raises(CheckFailed):
        check_telegram(TG_TOKEN, http)


# --- Slack ----------------------------------------------------------------------------


def test_slack_tokens_are_both_checked():
    http, seen = client_for(slack_service())
    checked = check_slack(SLACK_BOT, SLACK_APP, http)
    assert checked.values == {"bot_token": SLACK_BOT, "app_token": SLACK_APP, "team_id": "T123"}
    assert checked.meta == {"team": "Larkfield", "bot_user": "ava"}
    auth, conn = seen
    assert auth.url.path == "/api/auth.test" and auth.headers["Authorization"] == f"Bearer {SLACK_BOT}"
    assert conn.url.path == "/api/apps.connections.open" and conn.headers["Authorization"] == f"Bearer {SLACK_APP}"


def test_slack_tokens_with_surrounding_whitespace_are_trimmed_before_use():
    http, seen = client_for(slack_service())
    checked = check_slack(f"  {SLACK_BOT}\n", f"\t{SLACK_APP}  ", http)
    assert checked.values["bot_token"] == SLACK_BOT and checked.values["app_token"] == SLACK_APP
    assert [r.headers["Authorization"] for r in seen] == [f"Bearer {SLACK_BOT}", f"Bearer {SLACK_APP}"]


def test_slack_missing_team_id_is_stored_as_empty():
    http, _ = client_for(slack_service(auth={"ok": True}))
    assert check_slack(SLACK_BOT, SLACK_APP, http).values["team_id"] == ""


@pytest.mark.parametrize(
    ("bot", "app", "message"),
    [
        ("xapp-1-abc", SLACK_APP, "should start with xoxb-"),
        ("xoxp-user-token", SLACK_APP, "should start with xoxb-"),
        (SLACK_BOT, "xoxb-1-abc", "should start with xapp-"),
        (SLACK_BOT, "", "should start with xapp-"),
    ],
)
def test_slack_tokens_that_arent_the_right_kind_are_refused_without_a_call(bot, app, message):
    http, seen = client_for(slack_service())
    with pytest.raises(CheckFailed, match=message):
        check_slack(bot, app, http)
    assert seen == []


def test_slack_rejects_a_wrong_bot_token():
    http, _ = client_for(slack_service(auth={"ok": False, "error": "invalid_auth"}))
    with pytest.raises(CheckFailed, match="didn't accept the bot token") as exc:
        check_slack(SLACK_BOT, SLACK_APP, http)
    assert_no_secret(exc, SLACK_BOT, SLACK_APP)


def test_slack_rejects_a_wrong_app_token():
    http, _ = client_for(slack_service(conn={"ok": False, "error": "not_allowed_token_type"}))
    with pytest.raises(CheckFailed, match="didn't accept the app-level token") as exc:
        check_slack(SLACK_BOT, SLACK_APP, http)
    assert "Socket Mode" in str(exc.value)
    assert_no_secret(exc, SLACK_BOT, SLACK_APP)


@pytest.mark.parametrize(
    "handler",
    [
        raise_(httpx.ConnectError("dns failure")),
        raise_(httpx.ConnectTimeout("connect timed out")),
        respond(503, text="<html>Service unavailable</html>"),
        respond(200, text="definitely not json"),
    ],
    ids=["down", "timeout", "html-error-page", "not-json"],
)
def test_slack_unreachable_or_garbled(handler):
    http, _ = client_for(handler)
    with pytest.raises(CheckFailed, match="Couldn't reach Slack") as exc:
        check_slack(SLACK_BOT, SLACK_APP, http)
    assert_no_secret(exc, SLACK_BOT, SLACK_APP)


@pytest.mark.parametrize("body", [b"null", b"[]"], ids=["null", "list"])
def test_slack_json_that_isnt_an_object_is_a_plain_failure(body):
    http, _ = client_for(respond(200, content=body, headers={"Content-Type": "application/json"}))
    with pytest.raises(CheckFailed):
        check_slack(SLACK_BOT, SLACK_APP, http)


# --- encryption -----------------------------------------------------------------------


def _tamper(token: str, index: int) -> str:
    raw = bytearray(base64.b64decode(token))
    raw[index] ^= 0x01
    return base64.b64encode(bytes(raw)).decode()


def test_each_encryption_uses_a_fresh_nonce():
    a, b = crypto.encrypt(CLAUDE_KEY, "anthropic/api_key"), crypto.encrypt(CLAUDE_KEY, "anthropic/api_key")
    assert a != b
    assert crypto.decrypt(a, "anthropic/api_key") == crypto.decrypt(b, "anthropic/api_key") == CLAUDE_KEY


def test_empty_and_unicode_secrets_round_trip():
    for value in ["", "pässwörd ✓ 秘密"]:
        assert crypto.decrypt(crypto.encrypt(value, "x/y"), "x/y") == value


@pytest.mark.parametrize("index", [1, 12, 13, -1], ids=["nonce-start", "nonce-end", "ciphertext", "auth-tag"])
def test_tampered_ciphertext_is_refused(index):
    token = crypto.encrypt(CLAUDE_KEY, "anthropic/api_key")
    with pytest.raises(crypto.VaultError, match="can't be decrypted"):
        crypto.decrypt(_tamper(token, index), "anthropic/api_key")


def test_truncated_ciphertext_is_refused():
    raw = base64.b64decode(crypto.encrypt(CLAUDE_KEY, "anthropic/api_key"))
    for cut in (1, 13, 20, len(raw) - 1):
        with pytest.raises(crypto.VaultError):
            crypto.decrypt(base64.b64encode(raw[:cut]).decode(), "anthropic/api_key")


def test_unknown_format_version_is_refused():
    raw = base64.b64decode(crypto.encrypt(CLAUDE_KEY, "anthropic/api_key"))
    with pytest.raises(crypto.VaultError, match="Unknown secret format"):
        crypto.decrypt(base64.b64encode(b"\x02" + raw[1:]).decode(), "anthropic/api_key")
    with pytest.raises(crypto.VaultError, match="Unknown secret format"):
        crypto.decrypt("", "anthropic/api_key")


def test_slot_binding_is_exact():
    token = crypto.encrypt(TG_TOKEN, "telegram/bot_token")
    for other in ["telegram/bot_token ", "Telegram/bot_token", "telegram/", "slack/bot_token"]:
        with pytest.raises(crypto.VaultError):
            crypto.decrypt(token, other)


def test_garbage_that_isnt_base64_is_a_vault_error():
    with pytest.raises(crypto.VaultError):
        crypto.decrypt("abcde", "anthropic/api_key")


def test_a_new_master_key_cannot_read_old_secrets(monkeypatch):
    """There is no key rotation: secrets saved under one master key are unreadable under another."""
    token = crypto.encrypt(CLAUDE_KEY, "anthropic/api_key")
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"n" * 32).decode())
    crypto.master_key.cache_clear()
    assert crypto.master_key() == (b"n" * 32, "environment")
    with pytest.raises(crypto.VaultError, match="current master key"):
        crypto.decrypt(token, "anthropic/api_key")


def test_master_key_must_be_32_bytes(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"k" * 16).decode())
    crypto.master_key.cache_clear()
    with pytest.raises(crypto.VaultError, match="32 bytes"):
        crypto.master_key()


def test_master_key_in_the_environment_may_have_whitespace(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", "  " + base64.b64encode(b"w" * 32).decode() + "\n")
    crypto.master_key.cache_clear()
    assert crypto.master_key() == (b"w" * 32, "environment")


@pytest.fixture
def secret_file(monkeypatch, tmp_path):
    """SECRETS_MASTER_KEY_FILE as a server sets it: a Docker secret, root-owned and world-readable."""
    path = tmp_path / "vault_master_key"
    path.write_text(base64.b64encode(b"f" * 32).decode() + "\n")
    os.chmod(path, 0o444)
    monkeypatch.delenv("SECRETS_MASTER_KEY")
    monkeypatch.setenv("SECRETS_MASTER_KEY_FILE", str(path))
    monkeypatch.setattr(crypto, "_from_keyring", lambda: pytest.fail("a server must not touch the keychain"))
    crypto.master_key.cache_clear()
    return path


def test_master_key_from_a_secret_file(secret_file):
    assert crypto.master_key() == (b"f" * 32, "secret file")
    assert secret_file.read_text().strip() == base64.b64encode(b"f" * 32).decode()  # read, never rewritten


def test_the_environment_wins_over_the_secret_file(secret_file, monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"e" * 32).decode())
    assert crypto.master_key() == (b"e" * 32, "environment")


def test_a_missing_secret_file_is_an_error_not_a_new_key(secret_file, monkeypatch, tmp_path):
    """A typo in the path must stop the server, not mint a key that can't read the saved credentials."""
    gone = tmp_path / "nope"
    monkeypatch.setenv("SECRETS_MASTER_KEY_FILE", str(gone))
    monkeypatch.setenv("SUPERAIDE_KEY_FILE", str(tmp_path / "fallback.key"))
    with pytest.raises(crypto.VaultError, match="can't be read"):
        crypto.master_key()
    assert not gone.exists() and not (tmp_path / "fallback.key").exists()


@pytest.mark.parametrize(
    ("content", "error"),
    [("not base64 at all!", "isn't base64"), (base64.b64encode(b"s" * 16).decode(), "32 bytes")],
)
def test_a_bad_secret_file_is_refused(secret_file, content, error):
    os.chmod(secret_file, 0o644)
    secret_file.write_text(content)
    with pytest.raises(crypto.VaultError, match=error):
        crypto.master_key()


def test_a_bad_environment_key_says_how_to_make_one(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", "definitely not base64!")
    crypto.master_key.cache_clear()
    with pytest.raises(crypto.VaultError, match="openssl rand -base64 32"):
        crypto.master_key()


class FakeKeyring:
    """Stands in for the keyring module's get/set so the real keychain is never touched."""

    def __init__(self, stored: str | None = None) -> None:
        self.stored = stored
        self.writes: list[tuple[str, str]] = []

    def get_password(self, service: str, user: str) -> str | None:
        assert (service, user) == (crypto.KEYRING_SERVICE, crypto.KEYRING_USER)
        return self.stored

    def set_password(self, service: str, user: str, value: str) -> None:
        self.writes.append((service, user))
        self.stored = value


@pytest.fixture
def keychain(monkeypatch):
    import keyring

    fake = FakeKeyring()
    monkeypatch.delenv("SECRETS_MASTER_KEY")
    monkeypatch.setattr(keyring, "get_keyring", lambda: object())  # any working backend
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    crypto.master_key.cache_clear()
    return fake


def test_keychain_makes_a_key_once_and_reuses_it(keychain):
    key, where = crypto.master_key()
    assert where == "keychain" and len(key) == 32 and keychain.writes == [("superaide", "master-key")]
    crypto.master_key.cache_clear()
    assert crypto.master_key() == (key, "keychain") and len(keychain.writes) == 1


def test_no_keychain_backend_falls_back_to_the_key_file(keychain, monkeypatch, tmp_path):
    import keyring
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    monkeypatch.setenv("SUPERAIDE_KEY_FILE", str(tmp_path / "master.key"))
    assert crypto._from_keyring() is None
    assert crypto.master_key()[1] == "key file" and keychain.writes == []


def test_a_broken_keychain_falls_back_to_the_key_file(keychain, monkeypatch, tmp_path):
    import keyring

    def locked(*_):
        raise RuntimeError("keychain is locked")

    monkeypatch.setattr(keyring, "get_password", locked)
    monkeypatch.setenv("SUPERAIDE_KEY_FILE", str(tmp_path / "master.key"))
    assert crypto.master_key()[1] == "key file"


def test_key_file_is_reused_once_made(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRETS_MASTER_KEY")
    monkeypatch.setattr(crypto, "_from_keyring", lambda: None)
    path = tmp_path / "nested" / "master.key"
    monkeypatch.setenv("SUPERAIDE_KEY_FILE", str(path))
    first, _ = crypto.master_key()
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0
    crypto.master_key.cache_clear()
    assert crypto.master_key() == (first, "key file")
    assert os.stat(path).st_size > 0


# --- masking --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "masked"),
    [
        ("", "…"),
        ("a", "…a"),
        ("abcd", "…cd"),
        ("12345678", "…78"),
        ("  12345678  ", "…78"),  # trimmed first
        (CLAUDE_KEY, "sk-…4f2a"),
        (TG_TOKEN, "123…AAAA"),  # no dash: the first three characters
        (SLACK_BOT, "xoxb-…hijk"),
        ("abcdefghij-klmnop", "abc…mnop"),  # a dash after the first 8 characters is not a prefix
    ],
)
def test_mask(value, masked):
    assert crypto.mask(value) == masked
