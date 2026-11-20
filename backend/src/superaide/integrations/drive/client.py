"""Google Drive, Docs and Sheets over their REST APIs: search, read as text, create a draft, append to a draft.

Same OAuth client as Gmail and Calendar, asked for more with `include_granted_scopes`. Reading needs
`drive.readonly`; making a draft (a new Doc or Sheet) and adding text to a file Ava made needs `drive.file`, asked
for only when the owner turns drafts on. `drive.file` is the narrow scope: Google itself refuses it on any file
Ava did not create, so even a bug here could not touch the rest of the owner's Drive.

Nothing here decides *whether* to read or write: integrations/drive/skill.py and the owner's approvals do. Every call
takes an `httpx.Client`, so tests never reach Google. Nothing here deletes, overwrites, moves or shares a file.

File text is untrusted (written by whoever could edit the file): `clean_text` strips control characters and
caps the length before any of it can reach a model.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any
from urllib.parse import quote

import httpx

from superaide.integrations.google import client as google
from superaide.integrations.google.client import GoogleError

READONLY = "https://www.googleapis.com/auth/drive.readonly"
FILE = "https://www.googleapis.com/auth/drive.file"  # only files this app made; the draft scope
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
DOCS_API = "https://docs.googleapis.com/v1/documents"

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"
FOLDER = "application/vnd.google-apps.folder"
# What can be read as text, and how Google exports it.
EXPORT = {DOC: "text/plain", SHEET: "text/csv"}
PLAIN = ("text/plain", "text/markdown", "text/csv")
KINDS = {"doc": DOC, "sheet": SHEET}
SOURCE_MIME = {"doc": "text/plain", "sheet": "text/csv"}
LIST_FIELDS = "files(id,name,mimeType,modifiedTime,owners(displayName,emailAddress),shared,ownedByMe,webViewLink)"
META_FIELDS = "id,name,mimeType,modifiedTime,size,shared,ownedByMe,trashed,capabilities(canEdit),webViewLink"
MAX_DOWNLOAD = 2_000_000  # bytes of one file taken from Google, before any cutting down
ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")


class NotFound(GoogleError):
    """Drive says the file isn't there or this sign-in may not see it."""


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=20)


def valid_id(value: str) -> bool:
    return bool(ID_RE.match(value or ""))


def clean_text(text: str, limit: int) -> str:
    """No control or bidi-override characters, no runs of blank lines, at most `limit` characters."""
    out = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out if len(out) <= limit else out[: max(0, limit - 1)].rstrip() + "…"


def query_for(text: str, *, name_only: bool = False) -> str:
    """A Drive `q` string built here, never taken from the model: the words are quoted and escaped, and trashed files,
    folders are left out."""
    words = " ".join(str(text or "").split())[:200]
    safe = words.replace("\\", "\\\\").replace("'", "\\'")
    parts = ["trashed = false", f"mimeType != '{FOLDER}'"]
    if safe:
        parts.insert(0, f"{'name' if name_only else 'fullText'} contains '{safe}'")
    return " and ".join(parts)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _json(r: httpx.Response, service: str) -> dict[str, Any]:
    if r.status_code in (404, 410):
        raise NotFound("Drive can't find that file, or Ava isn't allowed to see it.")
    try:
        data = r.json()
    except ValueError as exc:
        raise GoogleError(f"{service} sent something unreadable.") from exc
    if r.status_code != 200 or not isinstance(data, dict):
        google.raise_for_error(r, service)
    return data


def search(client: httpx.Client, token: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Files matching the words, most recently changed first. Metadata only."""
    params: dict[str, Any] = {
        "q": query, "pageSize": max(1, min(limit, 25)), "orderBy": "modifiedTime desc", "fields": LIST_FIELDS,
        "supportsAllDrives": "false", "corpora": "user",
    }  # fmt: skip
    try:
        r = client.get(f"{API}/files", params=params, headers=_headers(token))
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Google Drive just now.") from exc
    files = _json(r, "Google Drive").get("files")
    return [f for f in files if isinstance(f, dict) and f.get("id")] if isinstance(files, list) else []


def metadata(client: httpx.Client, token: str, file_id: str) -> dict[str, Any]:
    if not valid_id(file_id):
        raise NotFound("That isn't a Drive file id. Take it from a drive_search result.")
    try:
        r = client.get(
            f"{API}/files/{quote(file_id, safe='')}", params={"fields": META_FIELDS}, headers=_headers(token)
        )
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Google Drive just now.") from exc
    return _json(r, "Google Drive")


def _bounded(r: httpx.Response) -> str:
    if r.status_code in (404, 410):
        raise NotFound("Drive can't find that file, or Ava isn't allowed to see it.")
    if r.status_code != 200:
        google.raise_for_error(r, "Google Drive")
    if len(r.content) > MAX_DOWNLOAD:
        raise GoogleError("That file is too large to read here.")
    return r.content.decode("utf-8", errors="replace")


def read_text(client: httpx.Client, token: str, meta: dict[str, Any]) -> str | None:
    """The file's text (a Doc as plain text, a Sheet as CSV of its first tab, a small text file as is), or None when
    its type can't be read as text (an image, a PDF, a slide deck)."""
    file_id, mime = quote(str(meta["id"]), safe=""), str(meta.get("mimeType") or "")
    try:
        if mime in EXPORT:
            return _bounded(
                client.get(f"{API}/files/{file_id}/export", params={"mimeType": EXPORT[mime]}, headers=_headers(token))
            )
        if mime in PLAIN and int(meta.get("size") or 0) <= MAX_DOWNLOAD:
            return _bounded(client.get(f"{API}/files/{file_id}", params={"alt": "media"}, headers=_headers(token)))
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Google Drive just now.") from exc
    return None


def create_file(client: httpx.Client, token: str, *, name: str, kind: str, text: str) -> dict[str, Any]:
    """A new Google Doc (text) or Sheet (CSV) in the owner's Drive root. Never replaces anything: Drive gives the new
    file its own id."""
    boundary = secrets.token_hex(16)
    meta = json.dumps({"name": name, "mimeType": KINDS[kind]})
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
        f"--{boundary}\r\nContent-Type: {SOURCE_MIME[kind]}; charset=UTF-8\r\n\r\n{text}\r\n--{boundary}--"
    ).encode()
    try:
        r = client.post(
            UPLOAD, params={"uploadType": "multipart", "fields": "id,name,mimeType,webViewLink"},
            content=body, headers={**_headers(token), "Content-Type": f"multipart/related; boundary={boundary}"},
        )  # fmt: skip
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Google Drive just now.") from exc
    if r.status_code == 403:
        google.raise_for_error(
            r, "Google Drive", fallback="Google didn't allow that. Reconnect Drive with drafts on under Capabilities."
        )
    return _json(r, "Google Drive")


def append_text(client: httpx.Client, token: str, file_id: str, text: str) -> dict[str, Any]:
    """Add `text` at the end of a Google Doc (Docs API insertText). Only text is added; nothing is removed."""
    if not valid_id(file_id):
        raise NotFound("That isn't a Drive file id.")
    body = {"requests": [{"insertText": {"text": text, "endOfSegmentLocation": {"segmentId": ""}}}]}
    try:
        r = client.post(f"{DOCS_API}/{quote(file_id, safe='')}:batchUpdate", json=body, headers=_headers(token))
    except httpx.HTTPError as exc:
        raise GoogleError("Couldn't reach Google Docs just now.") from exc
    if r.status_code == 403:
        google.raise_for_error(
            r, "Google Docs", fallback="Google didn't allow that: Ava can only add to files she made herself."
        )
    return _json(r, "Google Docs")
