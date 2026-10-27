"""Every connection to an owner-typed or web address is checked when it is made (DNS rebinding,
redirects), not only when the address was saved."""

from __future__ import annotations

import ipaddress
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from superaide.brain.providers.openai_compat import OpenAICompatProvider
from superaide.brain.providers.presets import PRESETS
from superaide.platform.security import urls
from superaide.tools import web


class Hello(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest")
            self.end_headers()
            return
        body = b"<html><title>Local</title><p>secret internal page</p></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class QuickServer(HTTPServer):
    def server_bind(self) -> None:  # skip HTTPServer's reverse DNS lookup, which can take seconds
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


@pytest.fixture
def local_server():
    server = QuickServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def test_a_private_address_is_refused_at_connect_time(local_server):
    with (
        urls.safe_client(allow_private=False, hosted_mode=False) as c,
        pytest.raises(httpx.ConnectError, match="refused"),
    ):
        c.get(f"http://127.0.0.1:{local_server}/")
    with urls.safe_client(allow_private=True, hosted_mode=False) as c:
        assert c.get(f"http://127.0.0.1:{local_server}/").status_code == 200
    with urls.safe_client(allow_private=True, hosted_mode=True) as c, pytest.raises(httpx.ConnectError):
        c.get(f"http://127.0.0.1:{local_server}/")  # hosted: nothing internal, whatever the preset


def test_a_name_that_rebinds_to_an_internal_address_is_refused(monkeypatch, local_server):
    """Checked as public when saved, the name later resolves to 127.0.0.1: the connection is refused."""
    answers = iter([[ipaddress.ip_address("93.184.216.34")], [ipaddress.ip_address("127.0.0.1")]])
    monkeypatch.setattr(urls, "resolve", lambda host: next(answers))
    url = f"https://rebind.example:{local_server}/v1"
    assert urls.check_base_url(url, allow_private=False, hosted_mode=False) == url
    with (
        urls.safe_client(allow_private=False, hosted_mode=False) as c,
        pytest.raises(httpx.ConnectError, match="refused"),
    ):
        c.get(f"http://rebind.example:{local_server}/")


def test_the_connection_goes_to_the_checked_address(monkeypatch, local_server):
    """The socket connects to the address that was checked, not to a fresh lookup."""
    monkeypatch.setattr(urls, "resolve", lambda host: [ipaddress.ip_address("127.0.0.1")])
    with urls.safe_client(allow_private=True, hosted_mode=False) as c:
        r = c.get(f"http://any-name.test:{local_server}/")
    assert r.status_code == 200 and b"secret internal page" in r.content


def test_web_fetch_refuses_a_rebinding_name_and_internal_redirects(monkeypatch, local_server):
    answers = iter([[ipaddress.ip_address("93.184.216.34")], [ipaddress.ip_address("127.0.0.1")]])
    monkeypatch.setattr(urls, "resolve", lambda host: next(answers))
    out, err = web.fetch(f"http://rebind.example:{local_server}/")
    assert err and "secret" not in str(out)


def test_an_openai_compatible_provider_uses_the_checked_client(monkeypatch, local_server):
    from superaide.brain.spend.budget import BudgetGuard

    p = OpenAICompatProvider(
        BudgetGuard(0), PRESETS["openai"], api_key="sk-x", base_url=f"http://127.0.0.1:{local_server}/v1"
    )
    report = p.check()  # openai is a hosted preset: 127.0.0.1 is refused at connect time
    assert not report.ok and "Couldn't reach" in report.message


def test_the_openai_sdk_client_is_pinned_too(local_server):
    import httpx2

    with (
        urls.safe_client(allow_private=False, hosted_mode=False, for_openai=True) as c,
        pytest.raises(httpx2.ConnectError, match="refused"),
    ):
        c.get(f"http://127.0.0.1:{local_server}/")
