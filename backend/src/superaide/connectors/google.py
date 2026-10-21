"""Google Calendar, read-only: sign in with OAuth 2.1 + PKCE, then list events as citable sources.

The owner brings their own OAuth client (an unverified app used only by them avoids
Google's verification for a single user). The refresh token is encrypted in the vault;
access tokens are minted per use, cached in memory until shortly before they expire.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
PENDING_TTL = timedelta(minutes=10)


class GoogleError(Exception):
    pass


class ScopeNotGranted(GoogleError):
    """Signed in, but the calendar box on Google's consent screen was left unticked."""


def _client(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(timeout=15)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",  # a refresh token, so Ava can read later without you
                "prompt": "consent",
                "include_granted_scopes": "true",
            }
        )
    )


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    try:
        r = _client(client).post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleError("Couldn't reach Google. Try again in a minute.") from exc
    if r.status_code != 200 or not isinstance(data, dict) or "refresh_token" not in data:
        raise GoogleError("Google didn't give Ava access. Try connecting again, and allow calendar access.")
    granted = set(str(data.get("scope", "")).split())
    if not set(SCOPES) <= granted:
        raise ScopeNotGranted("Calendar access wasn't granted. Tick the calendar box on Google's screen.")
    return data


@dataclass
class _Token:
    value: str
    expires: float


_cache: dict[str, _Token] = {}
_lock = threading.Lock()


def access_token(client_id: str, client_secret: str, refresh_token: str, client: httpx.Client | None = None) -> str:
    key = hashlib.sha256(refresh_token.encode()).hexdigest()
    with _lock:
        cached = _cache.get(key)
        if cached and cached.expires > time.time() + 60:
            return cached.value
    try:
        r = _client(client).post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleError("Couldn't reach Google Calendar just now.") from exc
    if r.status_code != 200 or not isinstance(data, dict) or "access_token" not in data:
        raise GoogleError("Google Calendar access has expired or was removed. Reconnect it under Connections.")
    with _lock:
        _cache[key] = _Token(str(data["access_token"]), time.time() + int(data.get("expires_in", 3600)))
    return str(data["access_token"])


def list_events(
    token: str, *, start: datetime, end: datetime, query: str = "", limit: int = 20, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "timeMin": start.astimezone(UTC).isoformat(),
        "timeMax": end.astimezone(UTC).isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(limit, 50)),
    }
    if query:
        params["q"] = query[:200]
    try:
        r = _client(client).get(EVENTS_URL, params=params, headers={"Authorization": f"Bearer {token}"})
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleError("Couldn't reach Google Calendar just now.") from exc
    if r.status_code != 200 or not isinstance(data, dict):
        raise GoogleError(f"Google Calendar answered with an error ({r.status_code}).")
    items = data.get("items")
    return items if isinstance(items, list) else []


def event_text(event: dict[str, Any], tz: Any) -> tuple[str, str]:
    """(title line, one-line description) for a calendar event, in the owner's time zone."""
    start = event.get("start") or {}
    end = event.get("end") or {}
    if "dateTime" in start:
        s = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
        e = datetime.fromisoformat(end.get("dateTime", start["dateTime"])).astimezone(tz)
        when = f"{s:%a %d %b, %H:%M}–{e:%H:%M}"
    else:
        when = f"{start.get('date', '')} (all day)"
    title = str(event.get("summary") or "(no title)")[:200]
    bits = [f"{title}, {when}"]
    if event.get("location"):
        bits.append(f"at {str(event['location'])[:120]}")
    people = [str(a.get("displayName") or a.get("email")) for a in event.get("attendees") or [] if not a.get("self")]
    if people:
        bits.append("with " + ", ".join(people[:6]))
    return title, " ".join(bits)
