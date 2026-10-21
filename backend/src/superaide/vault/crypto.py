"""Envelope encryption for stored credentials.

Each secret is encrypted with AES-256-GCM under a master key, with the slot
("anthropic/api_key") as associated data, so a ciphertext can't be moved to
another slot. The master key never touches the database:

1. SECRETS_MASTER_KEY in the environment (base64, 32 bytes), for servers and tests;
2. otherwise SECRETS_MASTER_KEY_FILE, a file holding that same value (a Docker or
   systemd secret). It is only read, never created: if it is set but missing, that
   is an error, so a server never silently starts with a new key;
3. otherwise the operating system keychain (macOS Keychain) via `keyring`;
4. otherwise a key file readable only by you (~/.config/superaide/master.key).

On a headless Linux server there is no keychain, so set 1 or 2 there; otherwise
the key lands in a generated file next to the app, and losing it loses every
stored credential.

Limits: any program running as you can ask the keychain for the key. This
protects database dumps and backups, not a computer that is already compromised.
"""

from __future__ import annotations

import base64
import os
import secrets
import stat
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYRING_SERVICE = "superaide"
KEYRING_USER = "master-key"
VERSION = b"\x01"


class VaultError(Exception):
    pass


def _key_file() -> Path:
    return Path(os.environ.get("SUPERAIDE_KEY_FILE", Path.home() / ".config" / "superaide" / "master.key"))


def _from_keyring() -> bytes | None:
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if stored is None:
            stored = base64.b64encode(secrets.token_bytes(32)).decode()
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, stored)
        return base64.b64decode(stored)
    except Exception:
        return None


def _from_file() -> bytes:
    path = _key_file()
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise VaultError(f"{path} must be readable only by you (chmod 600).")
        return base64.b64decode(path.read_text().strip())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(key).decode())
    return key


def _from_secret_file(path: str) -> bytes:
    try:
        text = Path(path).read_text().strip()
    except OSError as exc:
        raise VaultError(f"SECRETS_MASTER_KEY_FILE points to {path}, which can't be read.") from exc
    return _decode(text, "SECRETS_MASTER_KEY_FILE")


def _decode(text: str, source: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except ValueError as exc:  # binascii.Error is a ValueError
        raise VaultError(f"{source} isn't base64. Make one with: openssl rand -base64 32") from exc


@lru_cache
def master_key() -> tuple[bytes, str]:
    """The 32-byte master key and where it lives (for the status screen)."""
    env = os.environ.get("SECRETS_MASTER_KEY", "").strip()
    secret_file = os.environ.get("SECRETS_MASTER_KEY_FILE", "").strip()
    if env:
        key, where = _decode(env, "SECRETS_MASTER_KEY"), "environment"
    elif secret_file:
        key, where = _from_secret_file(secret_file), "secret file"
    elif (k := _from_keyring()) is not None:
        key, where = k, "keychain"
    else:
        key, where = _from_file(), "key file"
    if len(key) != 32:
        raise VaultError("The master key must be 32 bytes.")
    return key, where


def encrypt(plaintext: str, slot: str) -> str:
    key, _ = master_key()
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), slot.encode())
    return base64.b64encode(VERSION + nonce + ct).decode()


def decrypt(token: str, slot: str) -> str:
    key, _ = master_key()
    try:
        raw = base64.b64decode(token, validate=True)
    except ValueError as exc:  # binascii.Error is a ValueError
        raise VaultError("Unknown secret format.") from exc
    if raw[:1] != VERSION:
        raise VaultError("Unknown secret format.")
    try:
        return AESGCM(key).decrypt(raw[1:13], raw[13:], slot.encode()).decode()
    except Exception as exc:
        raise VaultError("This secret can't be decrypted with the current master key.") from exc


def mask(value: str) -> str:
    """Enough to recognise a key, never enough to use it."""
    value = value.strip()
    if len(value) <= 8:
        return "…" + value[-2:]
    prefix = value.split("-")[0] + "-" if "-" in value[:8] else value[:3]
    return f"{prefix}…{value[-4:]}"
