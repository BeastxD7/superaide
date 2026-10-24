"""Ava writes light Markdown (the app renders it). Chat channels each want their own format.

Telegram gets its HTML subset (https://core.telegram.org/bots/api#html-style), Slack gets
mrkdwn (https://docs.slack.dev/messaging/formatting-message-text). Both converters escape
everything first, so text that merely looks like HTML stays text, and only well-formed pairs
on one line become formatting; an unmatched `*` or `` ` `` stays a literal character.

The stored event keeps the Markdown; this only shapes what goes out over a channel.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

TELEGRAM_LIMIT = 4096  # characters per message, after entity parsing
SLACK_LIMIT = 3900  # Slack truncates long `text`; it recommends staying under 4000

_FENCE = re.compile(r"```[^\n`]*\n(.*?)(?:\n)?```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*(?=\S)([^\n]+?)(?<=\S)\*\*")
_BOLD_UNDER = re.compile(r"(?<!\w)__(?=\S)([^\n<>]+?)(?<=\S)__(?!\w)")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n<>]+?)(?<=[^\s*])\*(?![\w*])")
_ITALIC_UNDER = re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n<>]+?)(?<=[^\s_])_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)([^\n<>]+?)(?<=\S)~~")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t#]*$", re.M)
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.M)
_CITATION = re.compile(r"([ \t]?)\[(\d{1,3})\](?![(:]|\[[^\d])")
_TOKEN = re.compile(r"\x00(\d+)\x00")
_TAG = re.compile(r"<(/?)([a-z]+)(?:\s[^>]*)?>")


class _Stash:
    """Holds finished pieces (code, links) out of the way of the emphasis rules."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def put(self, rendered: str) -> str:
        self.parts.append(rendered)
        return f"\x00{len(self.parts) - 1}\x00"

    def restore(self, text: str) -> str:
        for _ in range(3):  # a link's text may hold a code piece
            if "\x00" not in text:
                break
            text = _TOKEN.sub(lambda m: self.parts[int(m.group(1))], text)
        return text


def _convert(
    md: str,
    *,
    escape: Callable[[str], str],
    fence: Callable[[str], str],
    code: Callable[[str], str],
    link: Callable[[str, str], str],
    bold: str,
    italic: str,
    strike: str,
) -> str:
    md = md.replace("\x00", "")
    stash = _Stash()
    text = _FENCE.sub(lambda m: stash.put(fence(m.group(1))), md)
    text = _INLINE_CODE.sub(lambda m: stash.put(code(m.group(1))), text)
    text = _LINK.sub(lambda m: stash.put(link(m.group(1), m.group(2))), text)
    text = escape(text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)  # before italic: "* item" is a bullet
    open_, close = bold.split("{}")
    # Single-star italic before bold, so Slack's "*bold*" isn't then read as italic. The
    # italic rule never spans a "**", so the two can't half-overlap.
    text = _ITALIC_STAR.sub(lambda m: italic.format(m.group(1)), text)
    text = _HEADING.sub(lambda m: open_ + m.group(1).replace("**", "") + close, text)
    text = _BOLD.sub(lambda m: open_ + m.group(1) + close, text)
    text = _BOLD_UNDER.sub(lambda m: open_ + m.group(1) + close, text)
    text = _ITALIC_UNDER.sub(lambda m: italic.format(m.group(1)), text)
    text = _STRIKE.sub(lambda m: strike.format(m.group(1)), text)
    return stash.restore(text)


def _balanced(markup: str) -> bool:
    stack: list[str] = []
    for m in _TAG.finditer(markup):
        closing, name = m.group(1), m.group(2)
        if not closing:
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return not stack


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def to_telegram_html(md: str) -> str:
    """Markdown to Telegram's HTML subset. Never returns unbalanced tags."""
    out = _convert(
        md,
        escape=lambda t: html.escape(t, quote=False),
        fence=lambda body: f"<pre>{html.escape(body, quote=False)}</pre>",
        code=lambda body: f"<code>{html.escape(body, quote=False)}</code>",
        link=lambda text, url: f'<a href="{_attr(url)}">{html.escape(text, quote=False)}</a>',
        bold="<b>{}</b>",
        italic="<i>{}</i>",
        strike="<s>{}</s>",
    )
    return out if _balanced(out) else html.escape(to_plain(md), quote=False)


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_slack(md: str) -> str:
    """Markdown to Slack mrkdwn: *bold*, _italic_, ~strike~, <url|text>."""
    return _convert(
        md,
        escape=_slack_escape,
        fence=lambda body: f"```{_slack_escape(body)}```",
        code=lambda body: f"`{_slack_escape(body)}`",
        link=lambda text, url: f"<{_slack_escape(url).replace('|', '%7C')}|{_slack_escape(text).replace('|', '¦')}>",
        bold="*{}*",
        italic="_{}_",
        strike="~{}~",
    )


def to_plain(md: str) -> str:
    """A readable plain-text version, for when a channel refuses the formatted one."""
    text = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", md)
    text = _FENCE.sub(lambda m: m.group(1), text)
    text = _BOLD.sub(lambda m: m.group(1), text)
    text = _HEADING.sub(lambda m: m.group(1), text)
    return _BULLET.sub(lambda m: f"{m.group(1)}• ", text)


# --- citations -----------------------------------------------------------------------


def strip_citations(text: str) -> str:
    """Remove "[1]"-style marks (used where no sources list goes with the reply)."""

    def drop(m: re.Match[str]) -> str:
        before = text[m.start() - 1] if m.start() > 0 else ""
        if not m.group(1) and before.isalnum():
            return m.group(0)  # `items[1]` is not a citation
        return ""

    return _CITATION.sub(drop, text)


def _domain(url: str) -> str:
    host = urlsplit(url).hostname or url
    return host.removeprefix("www.")


def _label(title: str) -> str:
    return " ".join(title.split())[:80] or "Source"


_MEMORY_ID = re.compile(r"^ava://memory/([0-9a-fA-F-]{8,40})$")
_MAIL_ID = re.compile(r"^ava://mail/([A-Za-z0-9_-]{4,64})$")
SOURCE_NOTE = "[1] means a source: open its link to see where it came from."


def app_base() -> str:
    """Where the signed-in owner's app lives, for links in a channel message (never from model text)."""
    from superaide.config import get_settings

    s = get_settings()
    return (s.public_base_url or s.frontend_origin).rstrip("/")


def deep_link(src: dict[str, Any]) -> str:
    """A destination built in code from a stored source id: the owner's own Notebook entry or Gmail
    message. Empty when the source has none. The cited text and any model text never go into it."""
    uri = str(src.get("uri") or "")
    kind = src.get("kind")
    if kind == "web" and uri.startswith(("http://", "https://")):
        return uri
    if kind == "memory" and (m := _MEMORY_ID.match(uri)):
        return f"{app_base()}/dashboard/notebook#note-{m.group(1)}"
    if kind == "email" and (m := _MAIL_ID.match(uri)):
        return f"https://mail.google.com/mail/u/0/#all/{m.group(1)}"
    return ""


def source_entries(sources: Iterable[dict[str, Any]]) -> list[tuple[int, str, str]]:
    """(n, label, url) per source. Web sources are labelled by domain; notes and emails by title and
    carry a deep link into the app or Gmail (signed-in only); the rest show their title. The cited
    text is never included."""
    entries = []
    for i, src in enumerate(sources, start=1):
        n = int(src.get("n") or i)
        url = deep_link(src)
        label = _domain(url) if src.get("kind") == "web" and url else _label(str(src.get("title") or ""))
        entries.append((n, label, url))
    return entries


def telegram_footer(sources: Iterable[dict[str, Any]]) -> str:
    items = [
        f'[{n}] <a href="{_attr(url)}">{html.escape(label, quote=False)}</a>'
        if url
        else f"[{n}] {html.escape(label, quote=False)}"
        for n, label, url in source_entries(sources)
    ]
    if not items:
        return ""
    return "<i>Sources: " + " · ".join(items) + "\n" + html.escape(SOURCE_NOTE, quote=False) + "</i>"


def slack_footer(sources: Iterable[dict[str, Any]]) -> str:
    items = [
        f"[{n}] <{_slack_escape(url).replace('|', '%7C')}|{_slack_escape(label)}>"
        if url
        else f"[{n}] {_slack_escape(label)}"
        for n, label, url in source_entries(sources)
    ]
    return "_Sources: " + " · ".join(items) + "\n" + _slack_escape(SOURCE_NOTE) + "_" if items else ""


def plain_footer(sources: Iterable[dict[str, Any]]) -> str:
    items = [f"[{n}] {label} ({url})" if url else f"[{n}] {label}" for n, label, url in source_entries(sources)]
    return "Sources: " + " · ".join(items) + "\n" + SOURCE_NOTE if items else ""


# --- splitting -----------------------------------------------------------------------


def _blocks(md: str) -> list[str]:
    """Paragraphs, keeping a fenced code block whole even when it contains blank lines."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _cut(block: str, limit: int, size: Callable[[str], int]) -> list[str]:
    """Break one oversized block: by line, re-fencing code, then by characters."""
    lines = block.split("\n")
    fenced = len(lines) >= 2 and lines[0].lstrip().startswith("```") and lines[-1].strip() == "```"
    opener, inner = (lines[0], lines[1:-1]) if fenced else ("", lines)
    wrap = (lambda body: f"{opener}\n{body}\n```") if fenced else (lambda body: body)
    pieces: list[str] = []
    current: list[str] = []
    for line in inner:
        if size(wrap(line)) > limit:  # a single huge line: hard cut by characters
            if current:
                pieces.append(wrap("\n".join(current)))
                current = []
            step = max(1, limit - (size(wrap("")) if fenced else 0))
            while line:
                n = min(len(line), step)
                while n > 1 and size(wrap(line[:n])) > limit:
                    n //= 2
                pieces.append(wrap(line[:n]))
                line = line[n:]
            continue
        if current and size(wrap("\n".join([*current, line]))) > limit:
            pieces.append(wrap("\n".join(current)))
            current = []
        current.append(line)
    if current:
        pieces.append(wrap("\n".join(current)))
    return pieces


def split_markdown(md: str, limit: int, size: Callable[[str], int] = len) -> list[str]:
    """Markdown chunks whose converted size (`size`) fits `limit`, broken at paragraphs."""
    if size(md) <= limit:
        return [md]
    chunks: list[str] = []
    current = ""
    for block in _blocks(md):
        for piece in [block] if size(block) <= limit else _cut(block, limit, size):
            joined = f"{current}\n\n{piece}" if current else piece
            if current and size(joined) > limit:
                chunks.append(current)
                current = piece
            else:
                current = joined
    if current:
        chunks.append(current)
    return chunks or [md[:limit]]


def outgoing(
    md: str,
    sources: Iterable[dict[str, Any]] | None,
    *,
    render: Callable[[str], str],
    footer: Callable[[Iterable[dict[str, Any]]], str],
    limit: int,
) -> list[tuple[str, str]]:
    """(rendered, plain Markdown) per message to send, with the sources footer at the end."""
    out = [(render(chunk), chunk) for chunk in split_markdown(md, limit, lambda t: len(render(t)))]
    srcs = list(sources or [])
    if srcs:
        rendered, plain = footer(srcs), plain_footer(srcs)
        last, last_plain = out[-1]
        if len(last) + 2 + len(rendered) <= limit:
            out[-1] = (f"{last}\n\n{rendered}", f"{last_plain}\n\n{plain}")
        else:
            out.append((rendered, plain))
    return out
