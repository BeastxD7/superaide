"""Microsoft 365 over Microsoft Graph: OAuth (authorization code + PKCE, delegated permissions), Outlook mail.

The owner brings their own Entra app registration (client id and secret in the vault); the refresh token is theirs too.
Nothing here decides *whether* to read or send: integrations/microsoft/mail.py and the owner's approvals do. Every call
takes an `httpx.Client`, so tests never reach Microsoft. Nothing here deletes, moves, forwards or edits a message.

Mail text is untrusted (anyone can write it): `clean_text` strips markup, control characters and quoted history, and
`redact` blanks long numbers before any of it can reach a model.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from superaide.integrations.gmail.client import redact

AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
READ_SCOPES = ["offline_access", "User.Read", "Mail.Read"]
SEND_SCOPE = "Mail.Send"  # only when the owner turns sending on
CAL_READ_SCOPES = ["offline_access", "User.Read", "Calendars.Read"]
CAL_WRITE_SCOPE = "Calendars.ReadWrite"  # only when the owner turns calendar changes on
CLIENT_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_~.\-]{8,255}$")
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-=+/]{16,400}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")
_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
_QUOTE_START = re.compile(r"(?im)^(on .{5,120} wrote:|-{2,}\s*original message\s*-{2,}|from:\s.+\nsent:\s)")
_WORDS = re.compile(r"[^\w\s.\-@]", re.UNICODE)


class MicrosoftError(Exception):
    """Microsoft said no, or couldn't be reached. The message is safe to show: never a token or a request body."""


class NotFound(MicrosoftError):
    """It isn't there, or this sign-in may not see it."""


class AuthError(MicrosoftError):
    """The sign-in was refused, expired or lacks the permission."""


class ScopeMissing(MicrosoftError):
    """Signed in, but without the mail permission that was asked for."""


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=20)


def valid_client_id(value: str) -> bool:
    return bool(CLIENT_ID_RE.match(value or ""))


def valid_secret(value: str) -> bool:
    return bool(SECRET_RE.match(value or ""))


def valid_message_id(value: str) -> bool:
    return bool(MESSAGE_ID_RE.match(value or ""))


def valid_address(value: str) -> bool:
    return bool(EMAIL_RE.match(value or "")) and len(value) <= 254


def clean_text(text: str, limit: int, *, quoted: bool = False) -> str:
    """Plain text from a message body: no markup, control or bidi-override characters, no quoted earlier mail (unless
    `quoted`), numbers redacted, at most `limit` characters."""
    out = html.unescape(_TAGS.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")))
    out = _CONTROL.sub("", out)
    if not quoted and (m := _QUOTE_START.search(out)):
        out = out[: m.start()]
    lines = [ln.rstrip() for ln in out.splitlines() if not ln.lstrip().startswith(">")]
    out = re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", "\n".join(lines))).strip()
    out = redact(out)
    return out if len(out) <= limit else out[: max(0, limit - 1)].rstrip() + "…"


def search_words(words: str) -> str:
    """The text of a `$search`, built here and never taken as-is from the model: plain words only, so no KQL
    qualifier (`from:`, `to:`, a quote or a parenthesis) can widen it."""
    return " ".join(_WORDS.sub(" ", str(words or "")).replace("@", " ").split())[:200]


# --- OAuth --------------------------------------------------------------------------------


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str, scopes: list[str]) -> str:
    return (
        AUTHORITY
        + "/authorize?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "response_mode": "query",
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
    )


def _token_call(client: httpx.Client, data: dict[str, str], what: str) -> dict[str, Any]:
    try:
        r = client.post(f"{AUTHORITY}/token", data=data)
        body = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MicrosoftError("Couldn't reach Microsoft. Try again in a minute.") from exc
    if r.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
        reason = str(body.get("error") or "")[:60] if isinstance(body, dict) else ""
        if reason in ("invalid_grant", "interaction_required", "invalid_client", "unauthorized_client"):
            raise AuthError(f"Microsoft didn't accept the sign-in ({reason}). Reconnect under Capabilities → Outlook.")
        raise MicrosoftError(f"Microsoft didn't complete the {what} ({r.status_code}).")
    return body


def exchange_code(
    *, client_id: str, client_secret: str, code: str, verifier: str, redirect_uri: str, wanted: list[str],
    client: httpx.Client,
) -> tuple[str, list[str]]:  # fmt: skip
    """(refresh token, granted scopes). Raises ScopeMissing when a wanted mail permission wasn't granted."""
    body = _token_call(
        client,
        {
            "client_id": client_id, "client_secret": client_secret, "code": code, "code_verifier": verifier,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri, "scope": " ".join(wanted),
        },
        "sign-in",
    )  # fmt: skip
    granted = sorted({s.rsplit("/", 1)[-1] for s in str(body.get("scope", "")).split()})
    needed = {s for s in wanted if s.startswith(("Mail.", "Calendars."))}
    if not needed <= {g for g in granted}:
        raise ScopeMissing("The permission wasn't granted.")
    if not body.get("refresh_token"):
        raise MicrosoftError("Microsoft gave no refresh token. Make sure offline_access is allowed on the app.")
    return str(body["refresh_token"]), granted


def refresh(
    *, client_id: str, client_secret: str, refresh_token: str, scopes: list[str], client: httpx.Client
) -> tuple[str, str]:
    """(access token, the refresh token to keep: Microsoft rotates it on use)."""
    body = _token_call(
        client,
        {
            "client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token,
            "grant_type": "refresh_token", "scope": " ".join(scopes),
        },
        "refresh",
    )  # fmt: skip
    return str(body["access_token"]), str(body.get("refresh_token") or refresh_token)


# --- Graph --------------------------------------------------------------------------------


def _check(r: httpx.Response) -> dict[str, Any]:
    if r.status_code in (404, 410):
        raise NotFound("Outlook can't find that message.")
    if r.status_code == 401:
        raise AuthError("Microsoft didn't accept the sign-in. Reconnect under Capabilities → Outlook.")
    if r.status_code == 403:
        raise AuthError(
            "Microsoft didn't allow that: the mail permission is missing. Reconnect under Capabilities → Outlook."
        )
    if r.status_code == 429:
        raise MicrosoftError("Microsoft's own rate limit was reached. Try again later.")
    if r.status_code in (202, 204):
        return {}
    try:
        data = r.json()
    except ValueError as exc:
        raise MicrosoftError("Microsoft sent something unreadable.") from exc
    if r.status_code not in (200, 201) or not isinstance(data, dict):
        raise MicrosoftError(f"Microsoft answered {r.status_code}.")
    return data


def _call(client: httpx.Client, method: str, path: str, token: str, **kw: Any) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.body-content-type="text"'}
    try:
        return _check(client.request(method, f"{GRAPH}{path}", headers=headers, **kw))
    except httpx.HTTPError as exc:
        raise MicrosoftError("Couldn't reach Microsoft just now.") from exc


def me(client: httpx.Client, token: str) -> str:
    """The mailbox address the sign-in belongs to (also proves it works)."""
    data = _call(client, "GET", "/me", token, params={"$select": "mail,userPrincipalName"})
    who = data.get("mail") or data.get("userPrincipalName")
    if not isinstance(who, str) or not who:
        raise MicrosoftError("Microsoft didn't say whose mailbox that is.")
    return who


_LIST_FIELDS = "id,subject,from,receivedDateTime,bodyPreview,isRead,conversationId"


def search(client: httpx.Client, token: str, words: str, limit: int) -> list[dict[str, Any]]:
    params: dict[str, str] = {"$top": str(max(1, min(limit, 20))), "$select": _LIST_FIELDS}
    plain = search_words(words)
    if plain:
        params["$search"] = f'"{plain}"'
    else:
        params["$orderby"] = "receivedDateTime desc"  # ($search can't be ordered; Graph ranks it itself)
    data = _call(client, "GET", "/me/mailFolders/inbox/messages", token, params=params)
    items = data.get("value")
    return [m for m in items if isinstance(m, dict) and m.get("id")] if isinstance(items, list) else []


def message(client: httpx.Client, token: str, message_id: str) -> dict[str, Any]:
    if not valid_message_id(message_id):
        raise NotFound("That isn't a message id from a search result.")
    return _call(
        client, "GET", f"/me/messages/{quote(message_id, safe='')}", token,
        params={"$select": "id,subject,from,toRecipients,receivedDateTime,body,conversationId"},
    )  # fmt: skip


def send_mail(client: httpx.Client, token: str, to: list[str], subject: str, body: str) -> None:
    """A new message, sent now. Plain text only."""
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        },
        "saveToSentItems": True,
    }
    _call(client, "POST", "/me/sendMail", token, json=payload)


def reply(client: httpx.Client, token: str, message_id: str, body: str) -> None:
    """A reply to the sender of one message (never reply-all). Plain text only."""
    if not valid_message_id(message_id):
        raise NotFound("That isn't a message id from a search result.")
    _call(client, "POST", f"/me/messages/{quote(message_id, safe='')}/reply", token, json={"comment": body})


# --- calendar -----------------------------------------------------------------------------

_EVENT_FIELDS = "id,subject,start,end,isAllDay,showAs,organizer,location,isCancelled"


def _utc_header() -> dict[str, str]:
    return {"Prefer": 'outlook.timezone="UTC"'}


def calendar_view(client: httpx.Client, token: str, start: str, end: str, limit: int) -> list[dict[str, Any]]:
    """Events between two UTC instants (recurring ones expanded), earliest first."""
    headers = {"Authorization": f"Bearer {token}", **_utc_header()}
    try:
        r = client.get(
            f"{GRAPH}/me/calendarView", headers=headers,
            params={"startDateTime": start, "endDateTime": end, "$top": str(max(1, min(limit, 50))),
                    "$orderby": "start/dateTime", "$select": _EVENT_FIELDS},
        )  # fmt: skip
        data = _check(r)
    except httpx.HTTPError as exc:
        raise MicrosoftError("Couldn't reach Microsoft just now.") from exc
    items = data.get("value")
    return [e for e in items if isinstance(e, dict) and not e.get("isCancelled")] if isinstance(items, list) else []


def create_event(client: httpx.Client, token: str, subject: str, start: str, end: str) -> dict[str, Any]:
    """A new event on the owner's own calendar, with no attendees (so nobody is invited). Times are UTC."""
    body = {
        "subject": subject,
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
    }
    return _call(client, "POST", "/me/events", token, json=body)
