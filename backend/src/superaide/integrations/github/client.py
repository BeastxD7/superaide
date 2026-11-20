"""GitHub over its REST API: search issues and pull requests, read one with its comments, draft an issue, comment.

The owner's fine-grained personal access token is the credential (kept in the vault, shown only as a hint). Nothing
here decides *whether* to read or write: integrations/github/skill.py and the owner's approvals do. Every call takes an
`httpx.Client`, so tests never reach GitHub. Nothing here closes, edits, deletes, merges or reassigns anything.

Issue text is untrusted (anyone can write it): `clean_text` strips control characters and caps the length before any
of it can reach a model.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

API = "https://api.github.com"
REPO_RE = re.compile(r"^(?!\.+/)[A-Za-z0-9_.-]{1,100}/(?!\.+$)[A-Za-z0-9_.-]{1,100}$")  # no "." or ".." segment
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,255}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")
_WORDS = re.compile(r"[^\w\s.\-]", re.UNICODE)


class GithubError(Exception):
    """GitHub said no, or couldn't be reached. The message is safe to show: never the token or a request body."""


class NotFound(GithubError):
    """GitHub says it isn't there or this token may not see it."""


class AuthError(GithubError):
    """The token is wrong, expired or lacks the permission."""


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=20)


def valid_repo(value: str) -> bool:
    return bool(REPO_RE.match(value or ""))


def valid_token(value: str) -> bool:
    return bool(TOKEN_RE.match(value or ""))


def clean_text(text: str, limit: int) -> str:
    """No control or bidi-override characters, no runs of blank lines, at most `limit` characters."""
    out = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out if len(out) <= limit else out[: max(0, limit - 1)].rstrip() + "…"


def query_for(words: str, repos: list[str], kind: str = "") -> str:
    """A search `q` built here, never taken from the model: plain words only (a `repo:` or `org:` qualifier in the
    words would widen the search, so every qualifier character is dropped), then one `repo:` per allowed repo."""
    plain = " ".join(_WORDS.sub(" ", str(words or "")).split())[:200]
    parts = [plain] if plain else []
    if kind in ("issue", "pr"):
        parts.append(f"is:{kind}")
    parts += [f"repo:{r}" for r in repos]
    return " ".join(parts)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _check(r: httpx.Response) -> dict[str, Any] | list[Any]:
    if r.status_code in (404, 410):
        raise NotFound("GitHub can't find that, or the token isn't allowed to see it.")
    if r.status_code == 401:
        raise AuthError("GitHub didn't accept the token. The owner can paste a new one under Capabilities → GitHub.")
    if r.status_code == 429 or (r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
        raise GithubError("GitHub's own rate limit was reached. Try again later.")
    if r.status_code == 403:
        raise AuthError("GitHub didn't allow that: the token lacks the permission for it.")
    try:
        data = r.json()
    except ValueError as exc:
        raise GithubError("GitHub sent something unreadable.") from exc
    if r.status_code not in (200, 201) or not isinstance(data, dict | list):
        raise GithubError(f"GitHub answered {r.status_code}.")
    return data


def _call(client: httpx.Client, method: str, path: str, token: str, **kw: Any) -> dict[str, Any] | list[Any]:
    try:
        return _check(client.request(method, f"{API}{path}", headers=_headers(token), **kw))
    except httpx.HTTPError as exc:
        raise GithubError("Couldn't reach GitHub just now.") from exc


def viewer(client: httpx.Client, token: str) -> str:
    """The login the token belongs to (also proves the token works)."""
    data = _call(client, "GET", "/user", token)
    login = data.get("login") if isinstance(data, dict) else None
    if not isinstance(login, str) or not login:
        raise GithubError("GitHub didn't say who that token belongs to.")
    return login


def search(client: httpx.Client, token: str, q: str, limit: int) -> list[dict[str, Any]]:
    data = _call(
        client, "GET", "/search/issues", token, params={"q": q, "per_page": max(1, min(limit, 20)), "sort": "updated"}
    )
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict) and i.get("number")] if isinstance(items, list) else []


def issue(client: httpx.Client, token: str, repo: str, number: int) -> dict[str, Any]:
    if not valid_repo(repo) or number < 1:
        raise NotFound("That isn't a repository and issue number.")
    data = _call(client, "GET", f"/repos/{repo}/issues/{int(number)}", token)
    return data if isinstance(data, dict) else {}


def comments(client: httpx.Client, token: str, repo: str, number: int, limit: int) -> list[dict[str, Any]]:
    data = _call(
        client,
        "GET",
        f"/repos/{repo}/issues/{int(number)}/comments",
        token,
        params={"per_page": max(1, min(limit, 30))},
    )
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def create_issue(client: httpx.Client, token: str, repo: str, title: str, body: str) -> dict[str, Any]:
    """A new issue. Never edits or closes an existing one."""
    data = _call(client, "POST", f"/repos/{repo}/issues", token, json={"title": title, "body": body})
    return data if isinstance(data, dict) else {}


def create_comment(client: httpx.Client, token: str, repo: str, number: int, body: str) -> dict[str, Any]:
    """A new comment on an issue or pull request. Never edits or deletes one."""
    data = _call(client, "POST", f"/repos/{repo}/issues/{int(number)}/comments", token, json={"body": body})
    return data if isinstance(data, dict) else {}
