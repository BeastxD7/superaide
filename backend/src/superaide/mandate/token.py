"""Attenuating capability tokens (issue #112), macaroon style.

A token is a root (who signed, which instance, an id) plus a list of caveats, and a signature chained through
them: sig0 = HMAC(root_key, root); sig_i = HMAC(sig_{i-1}, caveat_i). Whoever holds a token can add a caveat
without knowing the root key, and every caveat can only restrict, because they are all checked together. The
holder can't remove one: that would need the signature before it. A verifier that meets a caveat type it does not
know denies (fails closed). The card the owner reads is rendered from these fields in code, never by a model.

Caveats:  tools [names]  | expires "ISO time" | max_amount number | recipient_domains [domains]
          max_uses int (counted by the service, not the token)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

KNOWN = {"tools", "expires", "max_amount", "recipient_domains", "max_uses"}


class MandateError(ValueError):
    pass


def _h(key: bytes, data: Any) -> bytes:
    return hmac.new(key, json.dumps(data, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).digest()


@dataclass(frozen=True)
class Token:
    mandate_id: str
    agent_id: str
    issuer: str  # the owner's person id
    caveats: tuple[tuple[str, Any], ...] = field(default_factory=tuple)
    sig: bytes = b""

    def root(self) -> dict[str, str]:
        return {"id": self.mandate_id, "agent": self.agent_id, "issuer": self.issuer}

    def caveat(self, name: str) -> list[Any]:
        return [v for n, v in self.caveats if n == name]


def issue(key: bytes, *, agent_id: uuid.UUID | str, issuer: uuid.UUID | str, **caveats: Any) -> Token:
    tok = Token(str(uuid.uuid4()), str(agent_id), str(issuer), (), b"")
    tok = Token(tok.mandate_id, tok.agent_id, tok.issuer, (), _h(key, tok.root()))
    for name, value in caveats.items():
        tok = narrow(tok, name, value)
    return tok


def _clean(name: str, value: Any) -> Any:
    if name not in KNOWN:
        raise MandateError(f"unknown constraint {name!r}")
    if name == "tools" or name == "recipient_domains":
        if not isinstance(value, (list, tuple)) or not value or not all(isinstance(v, str) and v for v in value):
            raise MandateError(f"{name} must be a non-empty list of names")
        return sorted({v.strip().lower() if name == "recipient_domains" else v.strip() for v in value})
    if name == "expires":
        return datetime.fromisoformat(str(value)).astimezone(UTC).isoformat()
    if name in {"max_amount", "max_uses"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise MandateError(f"{name} must be a number of at least 0")
        return value
    raise MandateError(f"unknown constraint {name!r}")  # pragma: no cover


def narrow(tok: Token, name: str, value: Any) -> Token:
    """A copy with one more restriction. Anything that would not make the token narrower is refused: the
    holder is told, instead of silently getting a no-op that looks like permission."""
    value = _clean(name, value)
    current = tok.caveat(name)
    if current:
        if name == "tools" or name == "recipient_domains":
            if not set(value) <= set(current[-1]):
                raise MandateError(f"can't widen {name}")
        elif name == "expires":
            if value > current[-1]:
                raise MandateError("can't extend the expiry")
        elif value > current[-1]:
            raise MandateError(f"can't raise {name}")
    return Token(tok.mandate_id, tok.agent_id, tok.issuer, (*tok.caveats, (name, value)), _h(tok.sig, [name, value]))


def verify_signature(key: bytes, tok: Token) -> bool:
    sig = _h(key, tok.root())
    for name, value in tok.caveats:
        sig = _h(sig, [name, value])
    return hmac.compare_digest(sig, tok.sig)


def dumps(tok: Token) -> str:
    body = {"r": tok.root(), "c": [list(c) for c in tok.caveats], "s": base64.urlsafe_b64encode(tok.sig).decode()}
    return base64.urlsafe_b64encode(json.dumps(body, sort_keys=True).encode()).decode()


def loads(text: str) -> Token:
    try:
        body = json.loads(base64.urlsafe_b64decode(text.encode()))
        r = body["r"]
        return Token(
            r["id"], r["agent"], r["issuer"], tuple((str(n), v) for n, v in body["c"]),
            base64.urlsafe_b64decode(body["s"].encode()),
        )  # fmt: skip
    except (ValueError, KeyError, TypeError) as exc:
        raise MandateError("not a mandate token") from exc


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


def authorize(
    key: bytes, tok: Token, *, agent_id: str, tool: str, args: dict[str, Any], now: datetime, uses: int = 0
) -> Verdict:
    """May this call run under the token? Every caveat must hold; any doubt is a no."""
    if not verify_signature(key, tok):
        return Verdict(False, "the signature doesn't match")
    if tok.agent_id != str(agent_id):
        return Verdict(False, "issued for another assistant")
    if not tok.caveat("tools") or not tok.caveat("expires"):
        return Verdict(False, "a mandate needs a tool list and an expiry")
    for name, value in tok.caveats:
        if name not in KNOWN:
            return Verdict(False, f"unknown constraint {name!r}")
        if name == "tools" and tool not in value:
            return Verdict(False, f"{tool} isn't covered")
        if name == "expires" and now >= datetime.fromisoformat(value):
            return Verdict(False, "expired")
        if name == "max_uses" and uses >= value:
            return Verdict(False, "used up")
        if name == "max_amount":
            amount = args.get("amount")
            if amount is None:
                continue  # the call carries no money
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount > value:
                return Verdict(False, "over the limit")
        if name == "recipient_domains":
            recipients = [str(r).lower() for r in (args.get("recipients") or [])]
            if any(r.rpartition("@")[2] not in value for r in recipients):
                return Verdict(False, "a recipient is outside the allowed domains")
    return Verdict(True)


_WORDS = {
    "tools": lambda v: "may use: " + ", ".join(v),
    "expires": lambda v: "until " + v[:16].replace("T", " ") + " UTC",
    "max_amount": lambda v: f"at most {v:g}",
    "recipient_domains": lambda v: "only to " + ", ".join(v),
    "max_uses": lambda v: f"{v:g} time(s) at most",
}


def card(tok: Token) -> str:
    """The plain-language card, built from the fields (the same ones the gateway enforces)."""
    parts = [_WORDS[n](v) if n in _WORDS else f"unknown limit {n}" for n, v in tok.caveats]
    return "; ".join(parts) or "no limits written (this card allows nothing)"
