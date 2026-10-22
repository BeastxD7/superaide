"""A fake Google (OAuth token endpoint + Gmail API) behind httpx.MockTransport. Nothing reaches Google."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from superaide.connectors import gmail as api
from superaide.connectors import google
from superaide.db.session import session_scope
from superaide.skills import gmail, registry
from superaide.vault import store

OWNER_ADDR = "shashank@example.com"
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
        self.requests: list[httpx.Request] = []
        self.grant = f"{api.READONLY}"
        self.fail = False

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

    def draft_to(self, did: str) -> str:
        return next(ln for ln in self.drafts[did]["raw"].splitlines() if ln.startswith("To:"))


@pytest.fixture(name="gmail_fake")
def gmail_fake_fixture(monkeypatch) -> Iterator[FakeGoogle]:
    from superaide.vault import crypto

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
        "/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": "correct horse battery"}
    )
    assert r.status_code == 200
    assert c.post("/app/hire", json={"agent": "ava", "accepted_notice": True}).status_code == 200
    return c
