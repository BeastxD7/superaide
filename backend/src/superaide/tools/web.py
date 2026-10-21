"""Superaide's own web search and web fetch, for models without provider-run ones (ADR 0007, section 6).

Results come back as sources (`search_result` blocks with the page URL), so they're cited and
checked like everything else and mark the turn as tainted: web text is information, never
instructions. Fetching refuses any address inside our own network, re-checks every redirect,
and caps size and time. Each search is charged against the spend cap before it runs.
"""

from __future__ import annotations

import html
import re
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import httpx

from superaide.security.urls import UnsafeURL, check_base_url

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_BYTES = 2_000_000
MAX_TEXT = 12_000
MAX_REDIRECTS = 3

SEARCH_TOOL = {
    "name": "web_search",
    "description": "Search the web. Use only for the outside world or something current. Returns pages to cite.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for"}},
        "required": ["query"],
        "additionalProperties": False,
    },
}
FETCH_TOOL = {
    "name": "web_fetch",
    "description": "Read one web page by its URL (from a search result or the owner). Returns its text to cite.",
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The page's full https:// address"}},
        "required": ["url"],
        "additionalProperties": False,
    },
}


def client() -> httpx.Client:
    """Replaced in tests with one that doesn't touch the network."""
    return httpx.Client(timeout=10, follow_redirects=False)


def source(url: str, title: str, text: str) -> dict[str, Any]:
    return {
        "type": "search_result",
        "source": url,
        "title": title[:200],
        "content": [{"type": "text", "text": text[:MAX_TEXT]}],
        "citations": {"enabled": True},
    }


def page_text(raw: str) -> tuple[str, str]:
    """(title, readable text) from HTML, without running or keeping anything active."""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    body = re.sub(r"<(script|style|noscript|svg|head)\b.*?</\1>", " ", raw, flags=re.I | re.S)
    body = re.sub(r"<(br|p|div|li|h[1-6]|tr)\b[^>]*>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    text = html.unescape(re.sub(r"[ \t\r\f\v]+", " ", body))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return html.unescape(title_m.group(1).strip()) if title_m else "", text


def fetch(url: str, http: httpx.Client | None = None) -> tuple[Any, bool]:
    """Returns (tool result content, is_error)."""
    http = http or client()
    current = url.strip()
    for _ in range(MAX_REDIRECTS + 1):
        try:
            current = check_base_url(current, allow_private=False, hosted_mode=True)
        except UnsafeURL as exc:
            return f"Can't open that address: {exc}", True
        try:
            with http.stream("GET", current, headers={"User-Agent": "Superaide/1.0 (+web_fetch)"}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    return f"The page answered with an error ({r.status_code}).", True
                kind = r.headers.get("content-type", "")
                if not any(t in kind for t in ("text/html", "text/plain", "application/xhtml")):
                    return f"That page isn't text ({kind or 'unknown type'}).", True
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
        except httpx.HTTPError:
            return "Couldn't reach that page.", True
        raw = body.decode(r.encoding or "utf-8", errors="replace")
        title, text = page_text(raw) if "html" in kind else ("", raw)
        return [source(current, title or current, text or "(The page has no readable text.)")], False
    return "That page redirects too many times.", True


def search(query: str, api_key: str, http: httpx.Client | None = None) -> tuple[Any, bool]:
    """Brave Search API. Returns (tool result content, is_error)."""
    if not query.strip():
        return "Say what to search for.", True
    try:
        r = (http or client()).get(
            SEARCH_URL,
            params={"q": query[:400], "count": 5},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
    except httpx.HTTPError:
        return "Couldn't reach the search service.", True
    if r.status_code >= 400:
        return f"The search service answered with an error ({r.status_code}).", True
    results = (r.json().get("web") or {}).get("results") or []
    found = [
        source(str(x.get("url", "")), str(x.get("title", "")), page_text(str(x.get("description", "")))[1])
        for x in results
        if str(x.get("url", "")).startswith("https://")
    ]
    return (found, False) if found else ("No results.", False)


class WebTools:
    """Superaide's web tools for one agent: which exist, and running them inside the spend cap."""

    def __init__(self, guard: Any, *, search_key: str = "", search_usd: Decimal = Decimal("0.01")) -> None:
        self.guard, self.search_key, self.search_usd = guard, search_key, search_usd

    def specs(self) -> dict[str, dict[str, Any]]:
        out = {"web_fetch": FETCH_TOOL}
        if self.search_key:
            out["web_search"] = SEARCH_TOOL
        return out

    def run(self, name: str, args: dict[str, Any]) -> tuple[Any, bool] | None:
        if name == "web_fetch":
            return fetch(str(args.get("url", "")))
        if name == "web_search" and self.search_key:
            entry = self.guard.reserve(
                purpose="web_research", model="web_search", worst_case_usd=self.search_usd, provider="brave"
            )
            try:
                result = search(str(args.get("query", "")), self.search_key)
            except Exception as exc:
                self.guard.fail(entry, f"{type(exc).__name__}: {exc}")
                raise
            self.guard.settle(entry, model="web_search", usage=None, request_id=None, provider="brave",
                              fixed_cost_usd=self.search_usd)  # fmt: skip
            return result
        return None
