"""Superaide's own web search and fetch for models without built-in ones (ADR 0007 WP8)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from superaide.brain.budget import BudgetGuard
from superaide.brain.llm import ProviderLLM
from superaide.brain.pricing import PriceRegistry
from superaide.db.models import SpendEntry
from superaide.db.session import session_scope
from superaide.skills.manifest import Tool
from superaide.tools import web
from tests.brain.test_openai_compat import PRICE, Server, completion, provider
from tests.pipeline.test_loop import owner_client  # noqa: F401  (fixture)

PAGE = "https://93.184.216.34/page"
HTML = (
    "<html><head><title>Weather &amp; more</title><script>evil()</script></head><body><p>Warm, 31°C.</p></body></html>"
)


def transport(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url).split("?")[0], httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)


@pytest.fixture
def net(monkeypatch):
    routes: dict[str, httpx.Response] = {}
    monkeypatch.setattr(web, "client", lambda: transport(routes))
    return routes


def test_a_page_is_read_as_text_without_its_scripts(net):
    net[PAGE] = httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
    [doc], err = web.fetch(PAGE)
    assert not err and doc["source"] == PAGE and doc["title"] == "Weather & more"
    assert "Warm, 31°C." in doc["content"][0]["text"] and "evil" not in doc["content"][0]["text"]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:8000/admin",
        "http://10.0.0.1/",
        "file:///etc/passwd",
    ],
)
def test_fetch_never_reaches_inside_our_network(net, url):
    out, err = web.fetch(url)
    assert err and "Can't open that address" in out


def test_every_redirect_is_checked_again(net):
    net[PAGE] = httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    out, err = web.fetch(PAGE)
    assert err and "Can't open that address" in out


def test_only_text_pages_are_read(net):
    net[PAGE] = httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    out, err = web.fetch(PAGE)
    assert err and "isn't text" in out


def test_a_search_is_charged_its_flat_price_before_it_runs(net):
    net[web.SEARCH_URL] = httpx.Response(
        200, json={"web": {"results": [{"url": PAGE, "title": "Weather", "description": "Warm <b>today</b>"}]}}
    )
    guard = BudgetGuard(10)
    tools = web.WebTools(guard, search_key="brave-key", search_usd=Decimal("0.005"))
    assert set(tools.specs()) == {"web_search", "web_fetch"}
    out = tools.run("web_search", {"query": "weather"})
    assert out is not None
    [doc], err = out
    assert not err and doc["source"] == PAGE and doc["content"][0]["text"] == "Warm today"
    with session_scope() as s:
        row = s.scalar(select(SpendEntry))
        assert row is not None and row.provider == "brave" and row.cost_usd == Decimal("0.005")
    assert web.WebTools(guard).specs().keys() == {"web_fetch"}  # no search key, no search


def test_a_search_past_the_cap_is_refused_without_calling_the_service(net):
    from superaide.brain.budget import BudgetExceeded

    tools = web.WebTools(BudgetGuard(0.001), search_key="k", search_usd=Decimal("0.01"))
    with pytest.raises(BudgetExceeded):
        tools.run("web_search", {"query": "x"})


def test_an_old_server_tool_manifest_still_loads_as_hosted():
    tool = Tool.model_validate(
        {"name": "web_search", "description": "x", "server_tool": "web_search_20250305", "effect": "read_public"}
    )
    assert tool.hosted == "web_search" and tool.server_tool is None


@pytest.mark.usefixtures("owner_client")
def test_a_model_without_web_search_reads_the_web_with_ours_and_cites_it(net):
    from tests.pipeline.test_loop import turn_for

    net[PAGE] = httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
    server = Server(
        completion(None, [("call_1", "web_fetch", {"url": PAGE})], "tool_calls"),
        completion(f"It's warm, 31°C [{PAGE}]."),
    )
    p, guard = provider(server, prices=PriceRegistry({("openai", "claude-haiku-5-5"): PRICE}))
    llm = ProviderLLM(p, web=web.WebTools(guard))
    tools: list[dict[str, Any]] = [{"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 3}]
    hi = [{"role": "user", "content": "hi"}]
    first = llm.step(purpose="chat_turn", model="claude-haiku-5-5", system=[], messages=hi, tools=tools, max_tokens=50)
    assert server.requests[0]["tools"][0]["function"]["name"] == "web_fetch"  # our tool stands in
    assert first.stop_reason == "tool_use"
    assert ProviderLLM(p, web=web.WebTools(guard)).run_substitute("web_fetch", {"url": PAGE}) is None  # never offered
    with session_scope() as s:
        turn = turn_for(s, lambda *a: None, web=True)
        turn.tools = lambda: tools  # type: ignore[method-assign]  # the web-research skill's hosted fetch
        turn.llm = llm
        server.responses = [
            completion(None, [("call_1", "web_fetch", {"url": PAGE})], "tool_calls"),
            completion(f"It's warm, 31°C [{PAGE}]."),
        ]
        result = turn.run([{"role": "user", "content": "What's the weather?"}])
    assert result.tainted and [c.uri for c in result.sources] == [PAGE] and result.sources[0].kind == "web"
    assert result.text.startswith("It's warm, 31°C [1]")
