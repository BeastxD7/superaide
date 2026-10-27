"""Password hashing (Argon2id) and the rules a new password must meet."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults follow RFC 9106: Argon2id, 3 passes, 64 MiB, 4 lanes.
_hasher = PasswordHasher()

# Spent on unknown emails so a login takes as long whether or not the account exists.
_DUMMY_HASH = _hasher.hash("not-a-real-password-just-for-timing")

MIN_LENGTH = 10
MAX_LENGTH = 256
_COMMON = {
    "password", "password1", "password123", "1234567890", "12345678910", "qwertyuiop",
    "iloveyou12", "letmein123", "welcome123", "admin12345", "abc1234567", "1q2w3e4r5t",
    "passw0rd12", "qwerty1234", "monkey1234", "dragon1234", "football12", "baseball12",
}  # fmt: skip


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> tuple[bool, bool]:
    """Returns (matches, needs_rehash). Always does the full amount of work."""
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except VerifyMismatchError, VerificationError, InvalidHashError:
        return False, False
    if password_hash is None:
        return False, False
    return True, _hasher.check_needs_rehash(password_hash)


def password_problem(password: str, *, email: str = "", name: str = "") -> str | None:
    """A plain-language reason the password can't be used, or None if it's fine."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    lowered = password.lower()
    if lowered in _COMMON or len(set(password)) < 4:
        return "That password is too easy to guess. Try a short sentence."
    local = email.split("@")[0].lower()
    if (local and len(local) >= 4 and local in lowered) or (name and name.lower().replace(" ", "") in lowered):
        return "Don't use your name or email in the password."
    return None
