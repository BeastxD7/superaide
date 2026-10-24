"""Google Calendar: sign in with OAuth 2.1 + PKCE, list events as citable sources and, once the owner
allows it (scope calendar.events, asked for incrementally), add, move and decline events.

The owner brings their own OAuth client (an unverified app used only by them avoids
Google's verification for a single user). The refresh token is encrypted in the vault;
access tokens are minted per use, cached in memory until shortly before they expire.
"""

from __future__ import annotations

import base64
import hashlib
import logging
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
FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
WATCH_URL = EVENTS_URL + "/watch"
STOP_URL = "https://www.googleapis.com/calendar/v3/channels/stop"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"  # change events: only with calendar actions on
PENDING_TTL = timedelta(minutes=10)


log = logging.getLogger(__name__)


class GoogleError(Exception):
    pass


class ApiDisabled(GoogleError):
    """Google answered 403 because the API (Gmail, Calendar) isn't switched on in the owner's Google Cloud project."""


class AccessDenied(GoogleError):
    """Google answered 403 because the stored sign-in lacks the permission (scope) the call needs."""


SCOPE_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficient_scope"}
RETRY_NOT = " This won't fix itself, so don't call this tool again until the owner has dealt with it; tell them."


def tool_text(exc: Exception) -> str:
    """What a chat tool tells the model about a Google failure. A 403 that needs the owner to act (API off,
    permission missing) says not to try again: the model used to repeat the same refused call."""
    return str(exc) + (RETRY_NOT if isinstance(exc, ApiDisabled | AccessDenied) else "")


DISABLED_REASONS = {"accessNotConfigured", "SERVICE_DISABLED", "API_KEY_SERVICE_BLOCKED"}


def error_reason(response: Any) -> str:
    """Google's reason code from an error answer (e.g. "accessNotConfigured"), or "". Never the request or any token."""
    try:
        err = response.json().get("error")
    except ValueError, AttributeError:
        return ""
    if isinstance(err, str):  # the token endpoint: {"error": "invalid_grant", ...}
        return err[:80]
    if not isinstance(err, dict):
        return ""
    for item in err.get("errors") or []:
        if isinstance(item, dict) and item.get("reason"):
            return str(item["reason"])[:80]
    for item in err.get("details") or []:
        if isinstance(item, dict) and item.get("reason"):
            return str(item["reason"])[:80]
    return str(err.get("status") or "")[:80]


def log_failure(what: str, response: Any) -> None:
    """Say in the log why Google refused (status and Google's reason code only: never a token, code or secret)."""
    log.warning("%s failed: HTTP %s %s", what, getattr(response, "status_code", "?"), error_reason(response))


def raise_for_error(response: Any, service: str, fallback: str = "") -> None:
    """GoogleError for a non-200 answer from `service`; ApiDisabled when the API isn't enabled in the project."""
    reason = error_reason(response)
    log_failure(f"{service} API call", response)
    if response.status_code == 403 and reason in DISABLED_REASONS:
        api = service if service.endswith("API") else f"{service} API"
        raise ApiDisabled(
            f"The {api} isn't switched on for your Google project. Enable it under APIs & Services, Library, "
            f"{api}, then ask me again."
        )
    if response.status_code == 403 and reason in SCOPE_REASONS:
        raise AccessDenied(
            f"Google didn't allow that: the {service} permission is missing or was removed. "
            "Reconnect Google under Capabilities, then ask me again."
        )
    if fallback:
        raise GoogleError(fallback)
    suffix = f", {reason}" if reason else ""
    raise GoogleError(f"{service} answered with an error ({response.status_code}{suffix}).")


class ScopeNotGranted(GoogleError):
    """Signed in, but the calendar box on Google's consent screen was left unticked."""


def _client(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(timeout=15)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str, scopes: list[str] | None = None) -> str:
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes or SCOPES),
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
    scopes: list[str] | None = None,
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
        log_failure("google token exchange", r)
        raise GoogleError("Google didn't give Ava access. Try connecting again, and allow calendar access.")
    granted = set(str(data.get("scope", "")).split())
    if not set(scopes or SCOPES) <= granted:
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
        log_failure("google token refresh", r)
        raise GoogleError("Google Calendar access has expired or was removed. Reconnect Google under Capabilities.")
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
        raise_for_error(r, "Google Calendar")
    items = data.get("items")
    return items if isinstance(items, list) else []


def get_event(token: str, event_id: str, client: httpx.Client | None = None) -> dict[str, Any] | None:
    """One event by id, or None when it's been deleted. Raises GoogleError when Google can't say."""
    from urllib.parse import quote

    try:
        url = f"{EVENTS_URL}/{quote(event_id, safe='')}"
        r = _client(client).get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Google Calendar just now.") from exc
    if r.status_code in (404, 410):
        return None
    try:
        data = r.json()
    except ValueError as exc:
        raise GoogleError("Google Calendar sent something unreadable.") from exc
    if r.status_code != 200 or not isinstance(data, dict):
        raise_for_error(r, "Google Calendar")
    return data


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


def _call(method: str, url: str, token: str, *, client: httpx.Client | None = None, **kw: Any) -> dict[str, Any]:
    """One Calendar API call that changes or asks something. Raises GoogleError with a plain reason."""
    try:
        r = _client(client).request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Google Calendar just now.") from exc
    if r.status_code in (404, 410):
        raise GoogleError("That event isn't in the calendar any more.")
    if r.status_code == 403:
        raise_for_error(
            r,
            "Google Calendar",
            fallback="Google didn't allow that change. Reconnect Google Calendar with calendar actions on.",
        )
    if r.status_code == 204:
        return {}
    try:
        data = r.json()
    except ValueError as exc:
        raise GoogleError("Google Calendar sent something unreadable.") from exc
    if r.status_code not in (200, 201) or not isinstance(data, dict):
        raise_for_error(r, "Google Calendar")
    return data


def free_busy(
    token: str, *, start: datetime, end: datetime, client: httpx.Client | None = None
) -> list[tuple[datetime, datetime]]:
    """The owner's busy intervals on their primary calendar, in order."""
    body = {"timeMin": start.astimezone(UTC).isoformat(), "timeMax": end.astimezone(UTC).isoformat(),
            "items": [{"id": "primary"}]}  # fmt: skip
    data = _call("POST", FREEBUSY_URL, token, client=client, json=body)
    busy = ((data.get("calendars") or {}).get("primary") or {}).get("busy") or []
    out = []
    for b in busy:
        try:
            out.append((datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])))
        except KeyError, ValueError, TypeError:
            continue
    return sorted(out)


def insert_event(
    token: str, body: dict[str, Any], *, notify: bool, client: httpx.Client | None = None
) -> dict[str, Any]:
    return _call("POST", EVENTS_URL, token, client=client, json=body,
                 params={"sendUpdates": "all" if notify else "none"})  # fmt: skip


def patch_event(
    token: str, event_id: str, body: dict[str, Any], *, notify: bool, client: httpx.Client | None = None
) -> dict[str, Any]:
    from urllib.parse import quote

    return _call("PATCH", f"{EVENTS_URL}/{quote(event_id, safe='')}", token, client=client, json=body,
                 params={"sendUpdates": "all" if notify else "none"})  # fmt: skip


def watch(
    token: str,
    *,
    channel_id: str,
    address: str,
    channel_token: str,
    ttl_seconds: int,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Ask Google to call `address` when the primary calendar's events change (events.watch)."""
    body = {"id": channel_id, "type": "web_hook", "address": address, "token": channel_token,
            "params": {"ttl": str(ttl_seconds)}}  # fmt: skip
    return _call("POST", WATCH_URL, token, client=client, json=body)


def stop_channel(token: str, *, channel_id: str, resource_id: str, client: httpx.Client | None = None) -> None:
    _call("POST", STOP_URL, token, client=client, json={"id": channel_id, "resourceId": resource_id})
