"""Linear over its GraphQL API: search issues, read one with its comments, draft an issue, comment.

The owner's personal API key is the credential (kept in the vault, shown only as a hint). Nothing here decides
*whether* to read or write: integrations/linear/skill.py and the owner's approvals do. Every call takes an
`httpx.Client`, so tests never reach Linear. Nothing here edits, closes, archives, deletes, assigns or moves anything.

Every query is a fixed document; model-chosen values travel only as GraphQL variables, never spliced into the query.
Issue text is untrusted (anyone in the workspace can write it): `clean_text` strips control characters and caps the
length before any of it can reach a model.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

API = "https://api.linear.app/graphql"
TOKEN_RE = re.compile(r"^lin_api_[A-Za-z0-9]{20,100}$")
TEAM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,9}$")
IDENT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{0,9})-([0-9]{1,7})$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")


class LinearError(Exception):
    """Linear said no, or couldn't be reached. The message is safe to show: never the key or a request body."""


class NotFound(LinearError):
    """Linear says it isn't there or this key may not see it."""


class AuthError(LinearError):
    """The key is wrong, revoked or lacks the permission."""


def http_client() -> httpx.Client:
    """Replaced in tests with a client that doesn't touch the network."""
    return httpx.Client(timeout=20)


def valid_token(value: str) -> bool:
    return bool(TOKEN_RE.match(value or ""))


def team_key(value: str) -> str | None:
    """A team key such as ENG, upper-cased, or None."""
    v = (value or "").strip()
    return v.upper() if TEAM_RE.match(v) else None


def identifier(value: Any) -> tuple[str, str] | None:
    """(team key, number) of an issue identifier such as ENG-12, or None."""
    m = IDENT_RE.match(str(value or "").strip())
    return (m.group(1).upper(), m.group(2).lstrip("0") or "0") if m else None


def clean_text(text: str, limit: int) -> str:
    """No control or bidi-override characters, no runs of blank lines, at most `limit` characters."""
    out = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out if len(out) <= limit else out[: max(0, limit - 1)].rstrip() + "…"


def _gql(client: httpx.Client, token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    try:
        r = client.post(API, headers={"Authorization": token}, json={"query": query, "variables": variables})
    except httpx.HTTPError as exc:
        raise LinearError("Couldn't reach Linear just now.") from exc
    if r.status_code == 401:
        raise AuthError("Linear didn't accept the key. The owner can paste a new one under Capabilities → Linear.")
    if r.status_code == 429:
        raise LinearError("Linear's own rate limit was reached. Try again later.")
    try:
        data = r.json()
    except ValueError as exc:
        raise LinearError("Linear sent something unreadable.") from exc
    errors = data.get("errors") if isinstance(data, dict) else None
    if errors:
        kinds = " ".join(
            str((e.get("extensions") or {}).get("type") or (e.get("extensions") or {}).get("code") or "")
            for e in errors
            if isinstance(e, dict)
        ).lower()
        if "authentication" in kinds or "forbidden" in kinds:
            raise AuthError("Linear didn't allow that: the key is wrong or lacks the permission.")
        if "ratelimit" in kinds.replace(" ", "").replace("_", ""):
            raise LinearError("Linear's own rate limit was reached. Try again later.")
        raise LinearError("Linear refused that request.")
    body = data.get("data") if isinstance(data, dict) else None
    if r.status_code != 200 or not isinstance(body, dict):
        raise LinearError(f"Linear answered {r.status_code}.")
    return body


def viewer(client: httpx.Client, token: str) -> str:
    """The name the key belongs to (also proves the key works)."""
    data = _gql(client, token, "query { viewer { name } }", {})
    name = (data.get("viewer") or {}).get("name")
    if not isinstance(name, str) or not name:
        raise LinearError("Linear didn't say who that key belongs to.")
    return name


_ISSUE_FIELDS = "id identifier title updatedAt url state { name } assignee { name } creator { name } team { key }"
SEARCH = (
    "query($filter: IssueFilter, $first: Int) { issues(filter: $filter, first: $first, orderBy: updatedAt) "
    "{ nodes { " + _ISSUE_FIELDS + " } } }"
)
READ = (
    "query($id: String!, $comments: Int) { issue(id: $id) { " + _ISSUE_FIELDS + " description "
    "comments(first: $comments) { nodes { body user { name } } } } }"
)
TEAMS = "query($key: String!) { teams(filter: { key: { eq: $key } }, first: 1) { nodes { id key name } } }"
CREATE_ISSUE = "mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { identifier } } }"
CREATE_COMMENT = "mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success } }"


def search(client: httpx.Client, token: str, words: str, teams: list[str], limit: int) -> list[dict[str, Any]]:
    """Issues in the given teams matching plain words. Teams come from the owner's list, never from the model."""
    flt: dict[str, Any] = {"team": {"key": {"in": teams}}}
    plain = " ".join(str(words or "").split())[:200]
    if plain:
        flt["searchableContent"] = {"contains": plain}
    data = _gql(client, token, SEARCH, {"filter": flt, "first": max(1, min(limit, 20))})
    nodes = (data.get("issues") or {}).get("nodes")
    return [n for n in nodes if isinstance(n, dict) and n.get("identifier")] if isinstance(nodes, list) else []


def issue(client: httpx.Client, token: str, ident: str, comments: int) -> dict[str, Any]:
    if identifier(ident) is None:
        raise NotFound("That isn't an issue identifier such as ENG-12.")
    data = _gql(client, token, READ, {"id": ident, "comments": max(0, min(comments, 30))})
    found = data.get("issue")
    if not isinstance(found, dict):
        raise NotFound("Linear can't find that, or the key isn't allowed to see it.")
    return found


def team_id(client: httpx.Client, token: str, key: str) -> str:
    data = _gql(client, token, TEAMS, {"key": key})
    nodes = (data.get("teams") or {}).get("nodes")
    if not isinstance(nodes, list) or not nodes or not isinstance(nodes[0], dict) or not nodes[0].get("id"):
        raise NotFound("Linear can't find that team, or the key isn't allowed to see it.")
    return str(nodes[0]["id"])


def create_issue(client: httpx.Client, token: str, team: str, title: str, body: str) -> dict[str, Any]:
    """A new issue in a team. Never edits or closes an existing one."""
    data = _gql(client, token, CREATE_ISSUE, {"input": {"teamId": team, "title": title, "description": body}})
    made = data.get("issueCreate") or {}
    if not made.get("success"):
        raise LinearError("Linear didn't create the issue.")
    return made.get("issue") or {}


def create_comment(client: httpx.Client, token: str, issue_id: str, body: str) -> None:
    """A new comment on an issue. Never edits or deletes one."""
    data = _gql(client, token, CREATE_COMMENT, {"input": {"issueId": issue_id, "body": body}})
    if not (data.get("commentCreate") or {}).get("success"):
        raise LinearError("Linear didn't add the comment.")
