"""Signed lesson files for sharing beyond this instance (#57).

A pluggable signer: the file carries the signer's public key and a signature over the canonical lesson
list, so any instance can check the file was not changed after the sender exported it. The built-in
provider ("local") is Ed25519 with a key derived from the vault master key; nothing is bought and no
service is called. A different provider (a hosted key service, say) is another class registered in
PROVIDERS and chosen with SCHOOL_SIGNER. A valid signature proves integrity and which key signed; it
does not make the text safe, so the lesson check runs again on import and imported text stays pending.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from superaide.vault.crypto import master_key


class Signer(Protocol):
    alg: str

    def public_key(self) -> str: ...
    def sign(self, payload: bytes) -> str: ...
    def verify(self, public_key: str, payload: bytes, signature: str) -> bool: ...


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class LocalEd25519:
    alg = "ed25519"

    def _private(self) -> Ed25519PrivateKey:
        root, _ = master_key()
        seed = hmac.new(root, b"superaide school lesson signing key v1", hashlib.sha256).digest()
        return Ed25519PrivateKey.from_private_bytes(seed)

    def public_key(self) -> str:
        return _b64(self._private().public_key().public_bytes_raw())

    def sign(self, payload: bytes) -> str:
        return _b64(self._private().sign(payload))

    def verify(self, public_key: str, payload: bytes, signature: str) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(_unb64(signature), payload)
        except InvalidSignature, ValueError:
            return False
        return True


PROVIDERS: dict[str, type[Signer]] = {"local": LocalEd25519}


def signer_for(name: str) -> Signer:
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown SCHOOL_SIGNER {name!r}; known: {', '.join(sorted(PROVIDERS))}.")
    return cls()


def key_id(public_key: str) -> str:
    """A short fingerprint the owner can read aloud to the other owner."""
    return hashlib.sha256(public_key.encode()).hexdigest()[:16]


def canonical(format_: str, lessons: list[dict[str, str]]) -> bytes:
    return json.dumps(
        {"format": format_, "lessons": lessons}, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def sign_document(signer: Signer, format_: str, lessons: list[dict[str, str]]) -> dict[str, Any]:
    pub = signer.public_key()
    return {
        "format": format_,
        "lessons": lessons,
        "signer": {"alg": signer.alg, "public_key": pub, "key_id": key_id(pub)},
        "signature": signer.sign(canonical(format_, lessons)),
    }
