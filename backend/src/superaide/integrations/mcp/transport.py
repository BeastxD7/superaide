"""Talking to an MCP server through the official SDK (`mcp`), one short connection per operation.

No connection outlives its operation: connect, list or call, close. That keeps the lifecycle trivial (a stdio
server is a child process that is gone when the operation ends) and means a server can never hold state in Ava
between turns.

The SDK is async (anyio); the loop, the routes and the database session are sync. Every operation therefore runs in its
own worker thread with its own event loop, under a hard timeout. The thread gets plain values only (`Target`): the
database session and the vault are read on the calling thread, never here.

Containment, all decided here and none by the server:
- stdio: the child gets a scrubbed environment (the SDK's short allow-list of non-secret variables such as PATH, with
  HOME pointed at an empty temporary directory) plus exactly the variables the owner stored for this server in the
  vault. Ava's own environment (API keys, DATABASE_URL, master key) is never passed on. The working directory is the
  same empty directory.
- http: the address is checked like any address the owner types (`platform.security.urls`), on every connection, so a
  name that later resolves to the private network or the cloud metadata service is refused. Headers come from the vault.
- the server cannot call back: sampling, elicitation and roots are not offered, so a server can never spend the owner's
  model budget or read their files through Ava.
- a result is cut to MAX_RESULT_CHARS and only its text is kept.
"""

from __future__ import annotations

import ipaddress
import logging
import tempfile
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpcore2
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp_types import TextContent

from superaide.mcp.api import tool_hash
from superaide.platform.security import urls

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 20_000  # about 5,000 tokens: a result can never flood the prompt (and the owner's spend)
MAX_TOOLS = 200
MAX_PAGES = 20
MAX_SCHEMA_CHARS = 50_000
MAX_ERROR_CHARS = 300


class McpError(Exception):
    """A plain-language reason an operation failed. Never carries a secret or a server's raw output."""


@dataclass(frozen=True)
class Target:
    """What one connection needs, as plain values."""

    transport: str  # stdio | http
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)  # the owner's own variables for this server, from the vault
    headers: dict[str, str] = field(default_factory=dict)
    timeout_s: int = 30
    hosted: bool = False  # many owners on one server: no stdio, no private addresses


@dataclass
class CallResult:
    text: str
    is_error: bool
    truncated: bool = False


# --- the guard on addresses, for the async client ---------------------------------------------------------


class _PinnedAsyncBackend(httpcore2.AnyIOBackend):
    """Like `urls.PinnedBackend` for the SDK's async client: check every address, connect to the checked one."""

    def __init__(self, *, allow_private: bool) -> None:
        self.allow_private = allow_private

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                          socket_options: Any = None) -> Any:  # fmt: skip
        try:
            ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = await anyio.to_thread.run_sync(
                urls.resolve, host
            )
            for ip in ips:
                urls.check_ip(ip, allow_private=self.allow_private)
        except urls.UnsafeURL as exc:
            raise httpcore2.ConnectError(f"refused {host}: {exc}") from exc
        return await super().connect_tcp(str(ips[0]), port, timeout, local_address, socket_options)


def check_url(url: str, *, hosted: bool) -> str:
    """The address cleaned, or McpError saying why it can't be used."""
    try:
        return urls.check_base_url(url, allow_private=True, hosted_mode=hosted)
    except urls.UnsafeURL as exc:
        raise McpError(str(exc)) from exc


def _http_client(target: Target) -> httpx2.AsyncClient:
    transport = httpx2.AsyncHTTPTransport(trust_env=False)
    transport._pool._network_backend = _PinnedAsyncBackend(allow_private=not target.hosted)  # noqa: SLF001
    return httpx2.AsyncClient(
        transport=transport,
        trust_env=False,
        headers=target.headers,
        timeout=httpx2.Timeout(float(target.timeout_s)),
        follow_redirects=False,
    )


# --- the client --------------------------------------------------------------------------------------------


def make_client(target: Target, home: str) -> Client:
    """The SDK client for a target. Tests replace `FACTORY` with a fake in-process server (no process, no network)."""
    if target.transport == "stdio":
        if target.hosted:
            raise McpError("Servers that run a command are not available on this hosted server.")
        return Client(
            StdioServerParameters(
                command=target.command,
                args=list(target.args),
                env={"HOME": home, **target.env},  # merged over the SDK's short, non-secret defaults (PATH, USER...)
                cwd=home,
            ),
            read_timeout_seconds=float(target.timeout_s),
        )
    if target.transport == "http":
        check_url(target.url, hosted=target.hosted)
        return Client(
            streamable_http_client(target.url, http_client=_http_client(target)),
            read_timeout_seconds=float(target.timeout_s),
        )
    raise McpError("Unknown server type.")


FACTORY: Callable[[Target, str], Client] = make_client


def _run[T](target: Target, work: Callable[[Client], Awaitable[T]]) -> T:
    """Connect, run `work`, disconnect, all in a fresh thread and event loop, inside the target's timeout."""

    async def main() -> T:
        with tempfile.TemporaryDirectory(prefix="superaide-mcp-") as home:
            with anyio.fail_after(target.timeout_s):
                async with FACTORY(target, home) as client:
                    return await work(client)

    def in_thread() -> T:
        return anyio.run(main)

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp")
    try:
        return pool.submit(in_thread).result(timeout=target.timeout_s + 10)
    except McpError:
        raise
    except (TimeoutError, FutureTimeout) as exc:
        raise McpError(f"The server did not answer within {target.timeout_s} seconds.") from exc
    except Exception as exc:  # the SDK, the OS (command not found) or the network; say what kind, never the details
        log.info("mcp operation failed: %s", type(exc).__name__, exc_info=True)
        raise McpError(_explain(exc)) from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _explain(exc: BaseException) -> str:
    if isinstance(exc, ExceptionGroup):
        for inner in exc.exceptions:
            return _explain(inner)
    if isinstance(exc, FileNotFoundError):
        return "The command was not found on this computer."
    if isinstance(exc, PermissionError):
        return "The command could not be run (permission denied)."
    if isinstance(exc, TimeoutError):
        return "The server did not answer in time."
    if isinstance(exc, McpError):
        return str(exc)
    text = f"The server could not be used ({type(exc).__name__})."
    return text[:MAX_ERROR_CHARS]


def tool_dict(tool: Any) -> dict[str, Any]:
    out = tool.model_dump(by_alias=True, exclude_none=True, mode="json")
    return {
        "name": str(out.get("name", ""))[:128],
        "description": str(out.get("description", ""))[:2000],
        "inputSchema": out.get("inputSchema") or {},
        "annotations": out.get("annotations") or {},
    }


def list_tools(target: Target) -> list[dict[str, Any]]:
    """The server's tools as `{name, description, inputSchema, annotations}`, walking pages up to a cap."""

    async def work(client: Client) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            page = await client.list_tools(cursor=cursor)
            out.extend(tool_dict(t) for t in page.tools)
            cursor = page.next_cursor
            if not cursor or len(out) >= MAX_TOOLS:
                break
        return out[:MAX_TOOLS]

    return _run(target, work)


def call_tool(target: Target, name: str, args: dict[str, Any], *, expected_hash: str) -> CallResult:
    """Call one tool, but only if the server still describes it exactly as the owner approved it (`expected_hash`,
    checked on this very connection). Returns the text only; images, resources and structured data are dropped."""

    async def work(client: Client) -> CallResult:
        listed: dict[str, Any] | None = None
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            page = await client.list_tools(cursor=cursor)
            listed = next((tool_dict(t) for t in page.tools if t.name == name), None)
            cursor = page.next_cursor
            if listed is not None or not cursor:
                break
        if listed is None or tool_hash(listed) != expected_hash:
            raise McpError("The server now describes this tool differently from what you approved. Approve it again.")
        res = await client.call_tool(name, args, read_timeout_seconds=float(target.timeout_s))
        parts: list[str] = []
        dropped = 0
        for block in res.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
            else:
                dropped += 1
        text = "\n".join(parts)
        if dropped:
            text += f"\n[{dropped} non-text item(s) from the tool were left out]"
        cut = len(text) > MAX_RESULT_CHARS
        if cut:
            text = text[:MAX_RESULT_CHARS] + "\n[result cut: too long]"
        return CallResult(text=text, is_error=bool(res.is_error), truncated=cut)

    return _run(target, work)
