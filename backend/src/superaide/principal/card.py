"""Principal Card (issue #135): a signed, checkable statement that an outbound message comes from an AI.

The card is a small JSON (the owner's first name and optional initial, what Ava can and cannot agree to, expiry)
signed with a key derived from the vault master key. The key is derived here and never returned, logged or stored.
The public page verifies the signature and expiry and shows only the card: it reads no memory, no contacts and no
relationships. The owner revokes every card at once by bumping a generation number kept in the instance's own
state.

Owner decision (2026-10-24): every email Ava sends carries the sign-off by default ("Regards, Ava, personal
assistant of <first name>" plus a fixed honest line and the card link). The owner can switch the default off in
Settings and can untick it for one email on that email's approval card (gmail_send); the choice is part of the
sealed approval, so it cannot change after the yes. PRINCIPAL_CARD_ENABLED is the global kill flag (on by
default). Telegram and Slack replies to other people carry the card only if the owner turned that on
(`channels`, default off). Counters (cards shown, distinct cards opened, cards revoked) hold numbers and card ids
only, never message text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from superaide.platform.config import get_settings
from superaide.platform.tenancy.context import InstanceScope, scope_or_default
from superaide.policy.killswitch import get_state, set_state
from superaide.vault.crypto import master_key

STATE_KEY = "principal_card"
STATS_KEY = "principal_card_stats"
MAX_OPENED = 2000
SENT_KEY = "principal_card_sent"  # the owner's own log of cards sent: id, channel, time, expiry (no message text)
MAX_SENT = 200
DEFAULT_CAN = ["Propose times", "Pass on messages"]
DEFAULT_CANNOT = ["Agree prices", "Sign anything", "Pay anyone"]
MAX_LISTED, MAX_ITEM, MAX_NAME = 8, 80, 80
MAX_FIRST = 40


def enabled() -> bool:
    return get_settings().principal_card_enabled


def _key() -> bytes:
    """HKDF-style (HMAC-SHA256) derivation, so the master key itself is never used as a signing key."""
    root, _ = master_key()
    return hmac.new(root, b"superaide principal card signing key v1", hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def clean_first_name(text: str) -> str:
    """One word, letters (any script) with an inner hyphen or apostrophe: a first name, not a full name."""
    word = text.strip()
    if not word or len(word) > MAX_FIRST or len(word.split()) != 1:
        return ""
    if not all(ch.isalpha() or ch in "-'\u2019" for ch in word) or not word[0].isalpha():
        return ""
    return word


def clean_initial(text: str) -> str:
    """Zero or one letter, with or without a trailing dot."""
    t = text.strip().rstrip(".").strip()
    return t.upper() if len(t) == 1 and t.isalpha() else ""


def display_of(first_name: str, initial: str) -> str:
    return f"{first_name} {initial}." if first_name and initial else first_name


DEFAULT_WORDING = "Regards,\n{agent}, personal assistant of {name}"
MAX_WORDING = 200


def clean_wording(text: str) -> str:
    """The owner's sign-off wording: plain text with {agent} and {name}, at most two short lines."""
    lines = [" ".join(ln.split()) for ln in text.replace("\r", "").split("\n")]
    out = "\n".join(ln for ln in lines if ln)[:MAX_WORDING]
    return out


def render_wording(wording: str, *, agent: str, name: str) -> str:
    return (wording or DEFAULT_WORDING).replace("{agent}", agent).replace("{name}", name)


def settings_of(session: Session) -> dict[str, Any]:
    s = get_state(session, STATE_KEY)
    first = clean_first_name(str(s.get("first_name") or ""))
    initial = clean_initial(str(s.get("initial") or "")) if first else ""
    return {
        "first_name": first,  # the owner's own choice; empty means "use the first name on the account"
        "initial": initial,
        "display_name": display_of(first, initial),
        "sign_default": s.get("sign_default") is not False,  # on unless the owner explicitly turned it off
        "channels": s.get("channels") is True,  # Telegram and Slack replies to others: off unless asked for
        "wording": clean_wording(str(s.get("wording") or "")) or DEFAULT_WORDING,
        "can": list(s.get("can") or DEFAULT_CAN),
        "cannot": list(s.get("cannot") or DEFAULT_CANNOT),
        "generation": int(s.get("generation") or 0),
        "revoked": [str(i) for i in (s.get("revoked") or [])][-MAX_SENT:],  # single cards the owner took back
    }


def owner_name(session: Session) -> str:
    """What the sign-off calls the owner: their chosen first name and initial, else the first name on the account."""
    cur = settings_of(session)
    if cur["display_name"]:
        return str(cur["display_name"])
    from superaide.app.accounts import owner

    person = owner(session)
    return clean_first_name((person.display_name or "").split(" ")[0]) if person else ""


def _clean(items: list[str]) -> list[str]:
    return [" ".join(i.split())[:MAX_ITEM] for i in items if i.strip()][:MAX_LISTED]


def configure(
    session: Session,
    *,
    first_name: str = "",
    initial: str = "",
    can: list[str] | None = None,
    cannot: list[str] | None = None,
    sign_default: bool | None = None,
    channels: bool | None = None,
    wording: str | None = None,
) -> dict[str, Any]:
    """Raises ValueError for a first name that is not a single word of letters (blank clears it)."""
    cur = settings_of(session)
    if first_name.strip():
        first = clean_first_name(first_name)
        if not first:
            raise ValueError("first_name must be one word of letters")
        cur["first_name"], cur["initial"] = first, clean_initial(initial)
    else:
        cur["first_name"], cur["initial"] = "", ""
    cur["display_name"] = display_of(cur["first_name"], cur["initial"])
    if can is not None:
        cur["can"] = _clean(can) or DEFAULT_CAN
    if cannot is not None:
        cur["cannot"] = _clean(cannot) or DEFAULT_CANNOT
    if sign_default is not None:
        cur["sign_default"] = sign_default
    if channels is not None:
        cur["channels"] = channels
    if wording is not None:
        cur["wording"] = clean_wording(wording) or DEFAULT_WORDING
    set_state(session, STATE_KEY, cur)
    return cur


def set_sign_default(session: Session, on: bool) -> dict[str, Any]:
    """The owner's own switch for the default. Off keeps every setting and adds nothing unless ticked per email."""
    cur = settings_of(session)
    cur["sign_default"] = on
    set_state(session, STATE_KEY, cur)
    return cur


def stats(session: Session) -> dict[str, Any]:
    """The experiment's numbers: cards shown (footers added), distinct cards opened, cards revoked."""
    st = get_state(session, STATS_KEY)
    shown, opened = int(st.get("shown") or 0), len(st.get("opened") or [])
    return {
        "shown": shown,
        "opened": opened,
        "revoked_one": int(st.get("revoked_one") or 0),
        "revoked_all": int(st.get("revoked_all") or 0),
        "open_rate": round(min(opened, shown) / shown, 3) if shown else 0.0,
    }


def _bump(session: Session, key: str) -> None:
    st = dict(get_state(session, STATS_KEY))
    st[key] = int(st.get(key) or 0) + 1
    set_state(session, STATS_KEY, st)


def record_open(session: Session, card_id: str) -> None:
    """Count a distinct valid card being opened (the same card opened twice counts once)."""
    st = dict(get_state(session, STATS_KEY))
    opened = list(st.get("opened") or [])
    if card_id in opened or len(opened) >= MAX_OPENED:
        return
    opened.append(card_id)
    st["opened"] = opened
    set_state(session, STATS_KEY, st)


def revoke_all(session: Session) -> int:
    cur = settings_of(session)
    cur["generation"] += 1
    set_state(session, STATE_KEY, cur)
    _bump(session, "revoked_all")
    return int(cur["generation"])


def issue(session: Session, *, now: float | None = None) -> str | None:
    """A signed token for the instance in scope, or None when the card is switched off globally."""
    if not enabled():
        return None
    cur = settings_of(session)
    scope = scope_or_default()
    t = int(time.time() if now is None else now)
    payload = {
        "v": 1, "t": str(scope.tenant_id), "a": str(scope.agent_id), "g": cur["generation"],
        "n": owner_name(session), "can": cur["can"], "cannot": cur["cannot"],
        "iat": t, "exp": t + get_settings().principal_card_days * 86400, "id": secrets.token_hex(6),
    }  # fmt: skip
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{body}.{_sign(body)}"


def scope_of(token: str) -> InstanceScope | None:
    """The instance named in a token, only if the signature holds (so it can be trusted to look up state)."""
    claims = _claims(token)
    if claims is None:
        return None
    try:
        return InstanceScope(tenant_id=uuid.UUID(claims["t"]), agent_id=uuid.UUID(claims["a"]))
    except KeyError, ValueError:
        return None


def _claims(token: str) -> dict[str, Any] | None:
    if len(token) > 4000 or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        claims = json.loads(_unb64(body))
    except ValueError:
        return None
    return claims if isinstance(claims, dict) and claims.get("v") == 1 else None


def verify(session: Session, token: str, *, now: float | None = None, record: bool = False) -> dict[str, Any] | None:
    """What the public page shows, or None for a forged, expired or revoked card. The session must be in the
    token's own instance scope (see `scope_of`)."""
    if not enabled():
        return None
    claims = _claims(token)
    if claims is None or float(claims.get("exp", 0)) < (time.time() if now is None else now):
        return None
    cur = settings_of(session)
    if int(claims.get("g", -1)) != cur["generation"] or str(claims.get("id")) in cur["revoked"]:
        return None
    if record:
        record_open(session, str(claims.get("id")))
    return {
        "ai": True,
        "agent": get_settings().agent_name,
        "acts_for": str(claims["n"]),
        "can": [str(x) for x in claims.get("can", [])],
        "cannot": [str(x) for x in claims.get("cannot", [])],
        "valid_until": int(claims["exp"]),
        "notice": (
            f"This message was written by {get_settings().agent_name}, an AI assistant, acting for {claims['n']}."
        ),
    }


def _block(session: Session, token: str) -> str:
    cur = settings_of(session)
    name = owner_name(session)
    agent = get_settings().agent_name
    base = get_settings().frontend_origin.rstrip("/")
    return (
        f"\n\n--\n{render_wording(cur['wording'], agent=agent, name=name)}\n\n"
        f"{agent} is an AI assistant acting for {name}. "
        f"What she may and may not agree to, and how to report a problem: {base}/c/{token}"
    )


def email_signature(session: Session) -> dict[str, str] | None:
    """The sign-off for an email: {"text", "id", "token", "label"}, or None when the card is off globally or no name is
    known. Nothing is counted here: the card counts as shown when the email is actually sent (`record_sent`)."""
    token = issue(session)
    if token is None or not owner_name(session):
        return None
    claims = _claims(token) or {}
    name = owner_name(session)
    return {
        "text": _block(session, token),
        "id": str(claims.get("id") or ""),
        "token": token,
        "label": f"Sign as {get_settings().agent_name}, personal assistant of {name}",
    }


def record_sent(session: Session, card_id: str, *, channel: str, to: str = "") -> None:
    """Note one card as shown (sent): the counter and the owner's sent list (id, channel, who by name, time,
    expiry; never the message)."""
    cur = settings_of(session)
    t = int(time.time())
    log = list(get_state(session, SENT_KEY).get("sent") or [])
    log.append(
        {"id": card_id, "channel": channel[:20], "to": " ".join(to.split())[:80], "at": t,
         "exp": t + get_settings().principal_card_days * 86400, "g": cur["generation"]}
    )  # fmt: skip
    set_state(session, SENT_KEY, {"sent": log[-MAX_SENT:]})
    _bump(session, "shown")


def footer(session: Session, *, channel: str = "", to: str = "") -> str:
    """The sign-off under a Telegram or Slack reply to someone who isn't the owner, or empty. Only if the owner
    turned the card on for those channels and left the default on. (Email goes through `email_signature`.)"""
    if not channel:
        return ""
    cur = settings_of(session)
    if not (cur["channels"] and cur["sign_default"]):
        return ""
    sig = email_signature(session)
    if sig is None:
        return ""
    record_sent(session, sig["id"], channel=channel, to=to)
    return sig["text"]


def sent(session: Session, *, now: float | None = None) -> list[dict[str, Any]]:
    """Cards sent, newest first, each with its status: active, expired or revoked."""
    cur = settings_of(session)
    t = time.time() if now is None else now
    out = []
    for e in reversed(list(get_state(session, SENT_KEY).get("sent") or [])):
        revoked = str(e.get("id")) in cur["revoked"] or int(e.get("g", 0)) < cur["generation"]
        out.append({**e, "status": "revoked" if revoked else "expired" if e.get("exp", 0) < t else "active"})
    return out


def revoke_one(session: Session, card_id: str) -> bool:
    """Take back a single card; False when it isn't one this instance sent."""
    if card_id not in {str(e.get("id")) for e in get_state(session, SENT_KEY).get("sent") or []}:
        return False
    cur = settings_of(session)
    if card_id not in cur["revoked"]:
        cur["revoked"].append(card_id)
        set_state(session, STATE_KEY, cur)
        _bump(session, "revoked_one")
    return True
