"""A fake Google (OAuth token endpoint + Gmail API) behind httpx.MockTransport. Nothing reaches Google."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from email import message_from_string
from email import policy as email_policy
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from superaide.identity.vault import store
from superaide.integrations.gmail import client as api
from superaide.integrations.gmail import skill as gmail
from superaide.integrations.google import client as google
from superaide.platform.db.session import session_scope
from superaide.skills import registry

OWNER_ADDR = "jordan@example.com"
CLIENT_ID = "1234-abc.apps.googleusercontent.com"
CLIENT_SECRET = "test-google-client-secret-9z"
REFRESH = "gmail-refresh-token-test-only"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def mail(
    mid: str,
    *,
    thread: str = "",
    sender: str = "Daniel Kim <daniel@acme.com>",
    subject: str = "Lunch on Friday?",
    body: str = "Are we still on for lunch on Friday?",
    labels: tuple[str, ...] = ("INBOX", "UNREAD"),
    dmarc: bool = True,
    extra: dict[str, str] | None = None,
    html: bool = False,
) -> dict[str, Any]:
    dom = sender.rsplit("@", 1)[-1].rstrip(">")
    hdrs = [
        {
            "name": "Authentication-Results",
            "value": f"mx.google.com; dkim=pass header.i=@{dom}; spf=pass; "
            + (f"dmarc=pass (p=REJECT) header.from={dom}" if dmarc else f"dmarc=fail header.from={dom}"),
        },
        {"name": "From", "value": sender},
        {"name": "To", "value": OWNER_ADDR},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Wed, 21 Oct 2026 09:30:00 +0000"},
        {"name": "Message-ID", "value": f"<{mid}@mail.example>"},
    ] + [{"name": k, "value": v} for k, v in (extra or {}).items()]
    return {
        "id": mid,
        "threadId": thread or f"t-{mid}",
        "labelIds": list(labels),
        "snippet": body[:100],
        "internalDate": "1792661400000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": hdrs,
            "parts": [{"mimeType": "text/html" if html else "text/plain", "body": {"data": b64(body)}}],
        },
    }


class FakeGoogle:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.added: list[tuple[int, str]] = []  # (history id, message id)
        self.history_id = 1000
        self.oldest_history = 0
        self.drafts: dict[str, dict[str, Any]] = {}
        self.sent: list[str] = []
        self.deleted: list[str] = []
        self.requests: list[httpx.Request] = []
        self.grant = f"{api.READONLY}"
        self.fail = False
        self.profile_error: httpx.Response | None = None  # what users/me/profile answers instead

    def add(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.messages[msg["id"]] = msg
        self.history_id += 1
        self.added.append((self.history_id, msg["id"]))
        return msg

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, r: httpx.Request) -> httpx.Response:
        self.requests.append(r)
        url, path = str(r.url), r.url.path
        if self.fail:
            return httpx.Response(500, json={})
        if url.startswith(google.TOKEN_URL):
            form = parse_qs(r.content.decode())
            if form.get("grant_type") == ["refresh_token"]:
                return httpx.Response(200, json={"access_token": "at-" + form["refresh_token"][0], "expires_in": 3600})
            return httpx.Response(
                200, json={"refresh_token": REFRESH, "access_token": "at-new", "scope": self.grant, "expires_in": 3600}
            )
        assert r.url.host == "gmail.googleapis.com", url
        assert r.headers["authorization"].startswith("Bearer at-")
        q = dict(r.url.params)
        if path.endswith("/profile"):
            if self.profile_error is not None:
                return self.profile_error
            return httpx.Response(200, json={"emailAddress": OWNER_ADDR, "historyId": str(self.history_id)})
        if path.endswith("/messages") and r.method == "GET":
            ids = [m for _, m in reversed(self.added)]
            if "is:unread" in q.get("q", ""):
                ids = [i for i in ids if "UNREAD" in self.messages[i]["labelIds"]]
            return httpx.Response(200, json={"messages": [{"id": i} for i in ids[: int(q.get("maxResults", 10))]]})
        if "/messages/" in path and r.method == "GET":
            m = self.messages.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=m) if m else httpx.Response(404, json={})
        if "/threads/" in path:
            tid = path.rsplit("/", 1)[1]
            ms = [m for m in self.messages.values() if m["threadId"] == tid]
            return httpx.Response(200, json={"id": tid, "messages": ms}) if ms else httpx.Response(404, json={})
        if path.endswith("/history"):
            start = int(q["startHistoryId"])
            if start < self.oldest_history:
                return httpx.Response(404, json={})
            rows = [
                {"messagesAdded": [{"message": {"id": m, "labelIds": self.messages[m]["labelIds"]}}]}
                for h, m in self.added
                if h > start
            ]
            return httpx.Response(200, json={"history": rows, "historyId": str(self.history_id)})
        if "/drafts/" in path and r.method == "DELETE":
            self.drafts.pop(path.rsplit("/", 1)[1], None)
            self.deleted.append(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        if "/drafts/" in path and r.method == "GET" and not path.endswith("/drafts/send"):
            did = path.rsplit("/", 1)[1]
            return (
                httpx.Response(200, json=self.draft_message(did))
                if did in self.drafts
                else httpx.Response(404, json={})
            )
        if path.endswith("/drafts/send"):
            did = json.loads(r.content)["id"]
            assert did in self.drafts
            self.sent.append(did)
            return httpx.Response(200, json={"id": f"sent-{did}", "threadId": "x"})
        if path.endswith("/drafts"):
            did = f"d{len(self.drafts) + 1}"
            body = json.loads(r.content)["message"]
            raw = base64.urlsafe_b64decode(body["raw"] + "=" * (-len(body["raw"]) % 4)).decode()
            self.drafts[did] = {"thread": body.get("threadId"), "raw": raw}
            return httpx.Response(200, json={"id": did, "message": {"id": f"m-{did}"}})
        if path.endswith("/messages/send"):
            raise AssertionError("Superaide must never send mail directly; only an approved draft")
        return httpx.Response(404, json={})

    def draft_message(self, did: str) -> dict[str, Any]:
        """drafts.get: the draft as Gmail holds it now (parsed from its raw MIME)."""
        em = message_from_string(self.drafts[did]["raw"], policy=email_policy.default)
        hdrs = [{"name": k, "value": str(v)} for k, v in em.items()]
        plain = em.get_body(preferencelist=("plain",))
        rich = em.get_body(preferencelist=("html",))
        text = plain.get_content() if plain else ""
        parts = [{"mimeType": "text/plain", "body": {"data": b64(text)}}]
        if rich is not None and rich.get_content_type() == "text/html":
            parts.append({"mimeType": "text/html", "body": {"data": b64(rich.get_content())}})
        payload: dict[str, Any] = {"mimeType": "text/plain", "headers": hdrs, "body": {"data": b64(text)}}
        if len(parts) > 1:  # multipart/alternative, as Gmail returns a draft with both parts
            payload = {"mimeType": "multipart/alternative", "headers": hdrs, "parts": parts}
        return {"id": did, "message": {"id": f"m-{did}", "threadId": self.drafts[did]["thread"], "payload": payload}}

    def edit_draft(self, did: str, *, text: str | None = None, add_header: tuple[str, str] | None = None) -> None:
        """Change a stored draft after it was made, as someone with access to the mailbox could."""
        em = message_from_string(self.drafts[did]["raw"], policy=email_policy.default)
        if text is not None:
            em.clear_content()  # a multipart draft becomes a single text part
            em.set_content(text)
        if add_header:
            em[add_header[0]] = add_header[1]
        self.drafts[did]["raw"] = em.as_string()

    def edit_html(self, did: str, html: str) -> None:
        """Change only the HTML part of a stored draft (the text part stays as it was)."""
        em = message_from_string(self.drafts[did]["raw"], policy=email_policy.default)
        em.get_body(preferencelist=("html",)).set_content(html, subtype="html")
        self.drafts[did]["raw"] = em.as_string()

    def draft_to(self, did: str) -> str:
        return next(ln for ln in self.drafts[did]["raw"].splitlines() if ln.startswith("To:"))


@pytest.fixture(name="gmail_fake")
def gmail_fake_fixture(monkeypatch) -> Iterator[FakeGoogle]:
    from superaide.identity.vault import crypto

    monkeypatch.setenv("SECRETS_MASTER_KEY", base64.b64encode(b"g" * 32).decode())
    monkeypatch.delenv("SECRETS_MASTER_KEY_FILE", raising=False)
    crypto.master_key.cache_clear()
    google._cache.clear()
    fake = FakeGoogle()
    monkeypatch.setattr(api, "http_client", fake.client)
    yield fake
    crypto.master_key.cache_clear()
    google._cache.clear()


def connect(*, scopes: tuple[str, ...] = (api.READONLY,), drafts: bool = False, install: bool = True) -> None:
    """The instance in scope: Google client saved, Gmail signed in with `scopes`, the capability installed."""
    with session_scope() as s:
        store.save(s, "google", {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}, user_id=None, meta={})
        store.save(
            s, "google", {"gmail_refresh_token": REFRESH}, user_id=None,
            meta={"scopes": list(scopes), "client_id": CLIENT_ID, "address": OWNER_ADDR},
        )  # fmt: skip
        if install:
            skill = registry.catalog()[gmail.SKILL_ID]
            row = registry.install(s, gmail.SKILL_ID, skill.manifest.permissions)
            row.settings = {"drafts": drafts}


@pytest.fixture(name="owner_client")
def owner_client_fixture(agent):
    """A signed-in owner who has hired Ava (the first hire: the instance tests run in)."""
    from fastapi.testclient import TestClient

    from superaide.main import create_app

    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    r = c.post(
        "/auth/signup", json={"name": "Jordan Whitfield", "email": "s@example.com", "password": "correct horse battery"}
    )
    assert r.status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c
