"""Notion over its REST API: search pages, read one as plain text, draft a new page, comment.

The owner's internal integration secret is the credential (kept in the vault, shown only as a hint). Nothing here
decides *whether* to read or write: integrations/notion/skill.py and the owner's approvals do. Every call takes an
`httpx.Client`, so tests never reach Notion. Nothing here edits, archives, deletes or moves anything.

Page text is untrusted (anyone the owner shares with can write it): `clean_text` strips control characters and caps the
length before any of it can reach a model.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
TOKEN_RE = re.compile(r"^(?:ntn|secret)_[A-Za-z0-9]{20,100}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")
_HEX32 = re.compile(r"(?:^|-)([0-9a-fA-F]{32})$")
_UUID = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")
CHUNK = 1900  # Notion refuses more than 2000 characters in one text object
MAX_BLOCKS = 90


class NotionError(Exception):
    """Notion said no, or couldn't be reached. The message is safe to show: never the token or a request body."""


class NotFound(NotionError):
    """Notion says it isn't there or this integration may not see it."""


class AuthError(NotionError):
    """The token is wrong, revoked or lacks the permission."""


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=20)


def valid_token(value: str) -> bool:
    return bool(TOKEN_RE.match(value or ""))


def page_id(value: str) -> str | None:
    """The id of a page from what the owner pasted (a notion.so link, a dashed id or 32 hex characters), as a
    lowercase dashed UUID, or None. A link to any other site is refused."""
    v = (value or "").strip()
    if not v or len(v) > 400:
        return None
    if "://" in v:
        parsed = urlparse(v)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not (
            host in ("notion.so", "notion.site") or host.endswith((".notion.so", ".notion.site"))
        ):
            return None
        seg = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        m = _UUID.search(seg) or _HEX32.search(seg)  # a link may carry a title slug before the id
    else:
        m = _UUID.fullmatch(v) or _HEX32.fullmatch(v)
    if m is None:
        return None
    try:
        return str(uuid.UUID(m.group(1)))
    except ValueError:
        return None


def clean_text(text: str, limit: int) -> str:
    """No control or bidi-override characters, no runs of blank lines, at most `limit` characters."""
    out = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out if len(out) <= limit else out[: max(0, limit - 1)].rstrip() + "…"


def plain(rich: Any) -> str:
    """The text of a Notion rich-text array."""
    if not isinstance(rich, list):
        return ""
    return "".join(str(r.get("plain_text") or "") for r in rich if isinstance(r, dict))


def title_of(obj: dict[str, Any]) -> str:
    """The title of a page (its title property) or database."""
    if isinstance(obj.get("title"), list):
        return plain(obj["title"])
    props = obj.get("properties")
    if isinstance(props, dict):
        for p in props.values():
            if isinstance(p, dict) and p.get("type") == "title":
                return plain(p.get("title"))
    return ""


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Notion-Version": VERSION}


def _check(r: httpx.Response) -> dict[str, Any]:
    if r.status_code == 404:
        raise NotFound("Notion can't find that, or the integration hasn't been shared with it.")
    if r.status_code == 401:
        raise AuthError("Notion didn't accept the token. The owner can paste a new one under Capabilities → Notion.")
    if r.status_code == 429:
        raise NotionError("Notion's own rate limit was reached. Try again later.")
    if r.status_code == 403:
        raise AuthError("Notion didn't allow that: the integration lacks the permission for it.")
    try:
        data = r.json()
    except ValueError as exc:
        raise NotionError("Notion sent something unreadable.") from exc
    if r.status_code != 200 or not isinstance(data, dict):
        raise NotionError(f"Notion answered {r.status_code}.")
    return data


def _call(client: httpx.Client, method: str, path: str, token: str, **kw: Any) -> dict[str, Any]:
    try:
        return _check(client.request(method, f"{API}{path}", headers=_headers(token), **kw))
    except httpx.HTTPError as exc:
        raise NotionError("Couldn't reach Notion just now.") from exc


def viewer(client: httpx.Client, token: str) -> str:
    """The name of the integration (also proves the token works)."""
    data = _call(client, "GET", "/users/me", token)
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise NotionError("Notion didn't say who that token belongs to.")
    return name


def search(client: httpx.Client, token: str, words: str, limit: int) -> list[dict[str, Any]]:
    """Pages (never databases) matching plain words, newest edit first. The query is a plain string: Notion has no
    search qualifiers."""
    body = {
        "query": " ".join(str(words or "").split())[:200],
        "page_size": max(1, min(limit, 20)),
        "filter": {"property": "object", "value": "page"},
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
    }
    data = _call(client, "POST", "/search", token, json=body)
    items = data.get("results")
    return [i for i in items if isinstance(i, dict) and i.get("id")] if isinstance(items, list) else []


def page(client: httpx.Client, token: str, pid: str) -> dict[str, Any]:
    return _call(client, "GET", f"/pages/{pid}", token)


def database(client: httpx.Client, token: str, did: str) -> dict[str, Any]:
    return _call(client, "GET", f"/databases/{did}", token)


def block(client: httpx.Client, token: str, bid: str) -> dict[str, Any]:
    return _call(client, "GET", f"/blocks/{bid}", token)


def children(client: httpx.Client, token: str, pid: str, limit: int) -> list[dict[str, Any]]:
    data = _call(client, "GET", f"/blocks/{pid}/children", token, params={"page_size": max(1, min(limit, 100))})
    items = data.get("results")
    return [b for b in items if isinstance(b, dict)] if isinstance(items, list) else []


TEXT_BLOCKS = (
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "to_do",
    "quote", "callout", "toggle", "code",
)  # fmt: skip


def block_text(b: dict[str, Any]) -> str:
    kind = str(b.get("type") or "")
    if kind not in TEXT_BLOCKS or not isinstance(b.get(kind), dict):
        return ""
    text = plain(b[kind].get("rich_text"))
    if not text:
        return ""
    prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list_item": "- ", "quote": "> "}
    return prefix.get(kind, "") + text


def _paragraphs(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        for i in range(0, len(para), CHUNK):
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": para[i : i + CHUNK]}}]},
                }
            )
    return blocks[:MAX_BLOCKS]


def create_page(client: httpx.Client, token: str, parent: str, title: str, body: str) -> dict[str, Any]:
    """A new page under a page. Never edits or moves an existing one."""
    payload = {
        "parent": {"page_id": parent},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        "children": _paragraphs(body),
    }
    return _call(client, "POST", "/pages", token, json=payload)


def create_comment(client: httpx.Client, token: str, pid: str, body: str) -> dict[str, Any]:
    """A new comment on a page. Never edits or deletes one."""
    parts = [{"type": "text", "text": {"content": body[i : i + CHUNK]}} for i in range(0, len(body), CHUNK)]
    return _call(client, "POST", "/comments", token, json={"parent": {"page_id": pid}, "rich_text": parts[:100]})
