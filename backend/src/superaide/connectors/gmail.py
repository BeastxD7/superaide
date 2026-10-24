"""Gmail over its REST API: incremental OAuth scopes, reading, drafting and sending a draft.

Same OAuth client as Google Calendar (connectors/google.py), asked for more with
`include_granted_scopes`. Reading needs `gmail.readonly`; drafting (and sending an
approved draft) needs `gmail.compose`, asked for only when the owner turns drafts on.
Nothing here decides *whether* to read or send: skills/gmail.py and the owner's
approvals do. Every call takes an `httpx.Client`, so tests never reach Google.

Email text is untrusted. `clean_body` keeps only what someone wrote in this message
(no quoted replies, signatures or HTML) and `redact` blanks long numbers (card,
account and one-time codes) before any of it can reach a model.
"""

from __future__ import annotations

import base64
import html
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from superaide.connectors import google
from superaide.connectors.google import AUTH_URL, TOKEN_URL, GoogleError

READONLY = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE = "https://www.googleapis.com/auth/gmail.compose"  # drafts, and sending a draft
API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_BODY = 1500  # characters of one message's own text that may reach the model


class ScopeMissing(GoogleError):
    """Google answered, but without the Gmail access that was asked for."""


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=15)


# --- OAuth (incremental) ------------------------------------------------------------------


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str, scopes: list[str]) -> str:
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",  # keep what was granted before (incremental)
            }
        )
    )


def exchange_code(
    *, client_id: str, client_secret: str, code: str, verifier: str, redirect_uri: str, wanted: list[str],
    client: httpx.Client,
) -> tuple[str, list[str], str]:  # fmt: skip
    """(refresh token, granted scopes, access token). Raises ScopeMissing when a wanted scope wasn't ticked."""
    try:
        r = client.post(
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
        google.log_failure("gmail token exchange", r)
        raise GoogleError("Google didn't give Ava access. Try connecting Gmail again.")
    granted = sorted(set(str(data.get("scope", "")).split()))
    if not set(wanted) <= set(granted):
        raise ScopeMissing("Gmail access wasn't granted. Tick the Gmail box on Google's screen.")
    return str(data["refresh_token"]), granted, str(data.get("access_token") or "")


# --- the API ------------------------------------------------------------------------------


def _get(client: httpx.Client, token: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """GET a Gmail resource. None on 404/410 (gone); GoogleError on anything else that isn't 200."""
    try:
        r = client.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Gmail just now.") from exc
    if r.status_code in (404, 410):
        return None
    try:
        data = r.json()
    except ValueError as exc:
        raise GoogleError("Gmail sent something unreadable.") from exc
    if r.status_code != 200 or not isinstance(data, dict):
        google.raise_for_error(r, "Gmail")
    return data


def _post(client: httpx.Client, token: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        r = client.post(f"{API}{path}", json=body, headers={"Authorization": f"Bearer {token}"})
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleError("Couldn't reach Gmail just now.") from exc
    if r.status_code != 200 or not isinstance(data, dict):
        google.raise_for_error(r, "Gmail")
    return data


def profile(client: httpx.Client, token: str) -> dict[str, Any]:
    """{emailAddress, historyId, …}"""
    return _get(client, token, "/profile") or {}


def search(client: httpx.Client, token: str, query: str, limit: int = 10) -> list[str]:
    """Message ids matching a Gmail search ("is:unread", "from:daniel"), newest first."""
    params: dict[str, Any] = {"maxResults": max(1, min(limit, 25))}
    if query:
        params["q"] = query[:300]
    data = _get(client, token, "/messages", params) or {}
    return [str(m["id"]) for m in data.get("messages") or [] if isinstance(m, dict) and m.get("id")]


def message(client: httpx.Client, token: str, message_id: str, fmt: str = "full") -> dict[str, Any] | None:
    return _get(client, token, f"/messages/{quote(message_id, safe='')}", {"format": fmt})


def thread(client: httpx.Client, token: str, thread_id: str) -> dict[str, Any] | None:
    return _get(client, token, f"/threads/{quote(thread_id, safe='')}", {"format": "full"})


class HistoryGone(GoogleError):
    """The saved history id is too old (Google keeps about a week): start again from now."""


def history(client: httpx.Client, token: str, start: str, limit: int = 100) -> tuple[list[str], str]:
    """(new inbox message ids, the latest history id) since `start`."""
    params = {"startHistoryId": start, "historyTypes": "messageAdded", "labelId": "INBOX", "maxResults": limit}
    data = _get(client, token, "/history", params)
    if data is None:
        raise HistoryGone("history expired")
    ids: list[str] = []
    for h in data.get("history") or []:
        for added in h.get("messagesAdded") or []:
            m = added.get("message") or {}
            labels = set(m.get("labelIds") or [])
            if m.get("id") and "INBOX" in labels and not labels & {"SENT", "DRAFT", "SPAM", "TRASH"}:
                ids.append(str(m["id"]))
    return list(dict.fromkeys(ids)), str(data.get("historyId") or start)


def create_draft(client: httpx.Client, token: str, raw: str, thread_id: str) -> dict[str, Any]:
    return _post(client, token, "/drafts", {"message": {"raw": raw, "threadId": thread_id}})


def get_draft(client: httpx.Client, token: str, draft_id: str) -> dict[str, Any] | None:
    """The draft as it is in Gmail right now (its message, in full), or None if it is gone."""
    data = _get(client, token, f"/drafts/{draft_id}", {"format": "full"})
    return (data.get("message") or None) if data else None


def delete_draft(client: httpx.Client, token: str, draft_id: str) -> None:
    """Discard a draft (the owner said no to sending it). A draft that is already gone is fine."""
    try:
        r = client.delete(f"{API}/drafts/{quote(draft_id, safe='')}", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Gmail just now.") from exc
    if r.status_code not in (200, 204, 404):
        google.raise_for_error(r, "Gmail")


def send_draft(client: httpx.Client, token: str, draft_id: str) -> dict[str, Any]:
    return _post(client, token, "/drafts/send", {"id": draft_id})


# --- reading a message ----------------------------------------------------------------------


def headers(msg: dict[str, Any]) -> dict[str, str]:
    """Lower-cased header name → the first value (Gmail puts its own Authentication-Results first)."""
    out: dict[str, str] = {}
    for h in (msg.get("payload") or {}).get("headers") or []:
        name = str(h.get("name", "")).lower()
        if name and name not in out:
            out[name] = str(h.get("value", ""))
    return out


def sender(msg: dict[str, Any]) -> tuple[str, str]:
    """(display name, normalised address) of From."""
    name, addr = parseaddr(headers(msg).get("from", ""))
    return name.strip().strip('"')[:120], addr.strip().lower()[:254]


def recipients(msg: dict[str, Any]) -> list[str]:
    h = headers(msg)
    return [a.lower() for _, a in getaddresses([h.get("to", ""), h.get("cc", "")]) if "@" in a]


def domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def authenticated(msg: dict[str, Any]) -> bool:
    """Whether Gmail checked the sender: DMARC passed for the From domain. A From line on its own
    proves nothing (anyone can type any address)."""
    results = headers(msg).get("authentication-results", "").lower()
    from_domain = domain(sender(msg)[1])
    m = re.search(r"\bdmarc=pass\b[^;]*?header\.from=([a-z0-9.-]+)", results)
    return bool(m and from_domain and m.group(1) == from_domain)


BULK_NAME = re.compile(r"(?:^|[._+-])(?:no-?reply|do-?not-?reply|newsletter|news|notifications?|mailer-daemon)@", re.I)


def bulk(msg: dict[str, Any]) -> bool:
    """A newsletter, list or machine: list headers, bulk precedence, a no-reply sender or Gmail's own tabs."""
    h = headers(msg)
    labels = set(msg.get("labelIds") or [])
    return bool(
        h.get("list-unsubscribe")
        or h.get("list-id")
        or h.get("precedence", "").lower() in {"bulk", "list", "junk"}
        or h.get("auto-submitted", "no").lower() != "no"
        or BULK_NAME.search(sender(msg)[1])
        or labels & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}
    )


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except ValueError, TypeError:
        return ""


def _parts(part: dict[str, Any]) -> list[dict[str, Any]]:
    out = [part]
    for p in part.get("parts") or []:
        out += _parts(p)
    return out


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


QUOTE_START = re.compile(
    r"^(?:On .{4,200}wrote:\s*$|-{2,} ?Original Message ?-{2,}|-{5,} ?Forwarded message ?-{5,}|From: .+|_{10,})",
    re.M,
)
SIGNATURE = re.compile(r"^(?:-- ?|Sent from my \w+.*)$", re.M)


def body_text(msg: dict[str, Any]) -> str:
    """The message's text: its text/plain part, else its HTML without tags."""
    parts = _parts(msg.get("payload") or {})
    for kind in ("text/plain", "text/html"):
        for p in parts:
            if str(p.get("mimeType", "")).lower() == kind and (p.get("body") or {}).get("data"):
                raw = _decode(str(p["body"]["data"]))
                return _strip_html(raw) if kind == "text/html" else raw
    return ""


def clean_body(text: str, limit: int = MAX_BODY) -> str:
    """Only what was written in this message: quoted earlier mail, '>' lines and the signature go."""
    m = QUOTE_START.search(text)
    if m:
        text = text[: m.start()]
    s = SIGNATURE.search(text)
    if s:
        text = text[: s.start()]
    lines = [ln.rstrip() for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    text = re.sub(r"[ \t]{2,}", " ", text)
    return redact(text[:limit] + ("…" if len(text) > limit else ""))


LONG_NUMBER = re.compile(r"(?<![\w])(?:\d[ -]?){5,}\d(?![\w])")  # 6 digits or more
URL_TOKEN = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*")


def redact(text: str) -> str:
    """Blank what a model never needs: long numbers (cards, accounts, codes) and link query strings
    (which carry sign-in and unsubscribe tokens)."""
    return URL_TOKEN.sub(r"\1", LONG_NUMBER.sub("[number]", text))


def subject(msg: dict[str, Any]) -> str:
    return redact(headers(msg).get("subject", "").strip()[:200]) or "(no subject)"


# --- writing a reply ----------------------------------------------------------------------


def new_raw(*, to: list[str], from_addr: str, subject: str, text: str) -> str:
    """A brand-new message (no thread), base64url-encoded for drafts.create."""
    m = EmailMessage()
    m["Subject"] = subject.strip()
    m["To"] = ", ".join(to)
    if from_addr:
        m["From"] = from_addr
    m.set_content(text)
    return base64.urlsafe_b64encode(m.as_bytes()).decode().rstrip("=")


def reply_raw(*, original: dict[str, Any], to: list[str], from_addr: str, text: str) -> str:
    """A reply in the original's thread, base64url-encoded for drafts.create."""
    h = headers(original)
    m = EmailMessage()
    subj = h.get("subject", "").strip()
    m["Subject"] = subj if subj.lower().startswith("re:") else f"Re: {subj}".strip()
    m["To"] = ", ".join(to)
    if from_addr:
        m["From"] = from_addr
    if h.get("message-id"):
        m["In-Reply-To"] = h["message-id"]
        m["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
    m.set_content(text)
    return base64.urlsafe_b64encode(m.as_bytes()).decode().rstrip("=")
