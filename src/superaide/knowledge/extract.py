"""Turn an uploaded file or a shared link into clean plain text plus a little metadata.

This module only extracts. It stores nothing and never calls the model. Anything
unsafe or unreadable is refused with an ExtractError whose message can be shown to
the owner as is.

Links are fetched carefully: http(s) only, standard ports only, no private or local
network addresses (checked again on every redirect and, with the default client,
again at connect time so a DNS answer can't change in between), a time limit, a size
limit, and only web-page content types. Exactly one page is read; nothing is crawled.
"""

from __future__ import annotations

import codecs
import html
import io
import ipaddress
import json
import os
import re
import socket
import time
import zipfile
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import docx
import httpcore
import httpx
import pypdf
import trafilatura
from docx.parts.hdrftr import HeaderPart

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
ALLOWED_FILE_TYPES = {".pdf", ".docx", ".txt", ".md"}

MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10.0  # per network step (connect, each read)
TOTAL_FETCH_SECONDS = 20.0  # wall clock for a whole extract_url call, redirects included
MAX_PDF_PAGES = 200
MAX_DOCX_UNPACKED_BYTES = 50 * 1024 * 1024  # guards against zip bombs posing as Word files
USER_AGENT = "AgiAgentBot/0.1 (personal assistant reading a page its owner shared)"
ALLOWED_PAGE_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})

LINKEDIN_MESSAGE = (
    "LinkedIn doesn't let apps read profiles. Paste your About and Experience sections, "
    "or upload your LinkedIn profile PDF (More → Save to PDF on your profile)."
)
X_MESSAGE = "X (Twitter) doesn't let apps read profiles without logging in. Paste your bio here instead."

_INVALID_URL = "That doesn't look like a valid web address."
_PRIVATE_ADDRESS = "That link points to a private or local network address, so it can't be read."
_UNKNOWN_HOST = "We couldn't find that website. Check the link for typos."
_TOO_SLOW = "That site took too long to respond. Try again later."
_CANT_CONNECT = "We couldn't connect to that site. Check the link, or try again later."
_DROPPED = "The connection to that site dropped before the page finished loading. Try again later."
_DAMAGED_PDF = "We couldn't read this PDF. It may be damaged; try exporting it as a PDF again."
_DAMAGED_DOCX = "We couldn't read this Word document. It may be damaged; try saving it as .docx again."
_GITHUB_BUSY = "GitHub is limiting requests right now. Try again in a little while, or paste your GitHub bio here."
_PAGE_ACCEPT = "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8"
_SHORT_PAGE_CHARS = 500


@dataclass(frozen=True)
class Extracted:
    """Clean text pulled from one file or link, ready to store or show."""

    text: str  # cleaned plain text, at most MAX_TEXT_CHARS (may be empty; warnings then say why)
    title: str  # file name or page title
    kind: str  # "pdf" | "docx" | "text" | "web" | "github"
    source: str  # original file name or final URL after redirects
    truncated: bool = False
    warnings: tuple[str, ...] = ()  # plain-language notes for the owner


class ExtractError(Exception):
    """Plain-language message safe to show the owner."""


# ---------------------------------------------------------------------------
# Text clean-up


_TRANSLATE: dict[int, str | None] = {
    **{ord(c): " " for c in "\t\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"},
    **{ord(c): " " for c in "\u202f\u205f\u3000"},
    **{ord(c): "\n" for c in "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"},
    **{ord(c): None for c in "\u00ad\u200b\u2060\ufeff"},  # soft hyphen, zero-width space, word joiner, BOM
    0xFB00: "ff",
    0xFB01: "fi",
    0xFB02: "fl",
    0xFB03: "ffi",
    0xFB04: "ffl",
    0xFB05: "st",
    0xFB06: "st",
}
_CONTROL_RE = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f]")


def _normalise(text: str) -> str:
    """Collapse runs of spaces, keep line and paragraph breaks, drop NULs and other control characters."""
    text = text.encode("utf-8", "replace").decode("utf-8")  # no lone surrogates (they break databases)
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_TRANSLATE)
    text = _CONTROL_RE.sub("", text)
    lines: list[str] = []
    for raw in text.split("\n"):
        line = " ".join(raw.split())
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def _one_line(value: object, limit: int) -> str:
    """Squash a short value (a title, a name) onto one clean line."""
    text = _CONTROL_RE.sub("", str(value).translate(_TRANSLATE))
    return " ".join(text.split())[:limit].strip()


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    cut = text[:MAX_TEXT_CHARS]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    if space >= MAX_TEXT_CHARS - 200:  # don't split a word if a break is close by
        cut = cut[:space]
    return cut.rstrip(), True


def _finish(
    raw: str,
    *,
    title: str,
    kind: str,
    source: str,
    warnings: Iterable[str] = (),
    cut: bool = False,
    cut_note: str | None = None,
    empty_note: str | None = None,
    short_note: str | None = None,
) -> Extracted:
    """Clean and cap the text, then attach the notes the owner should see."""
    text, clipped = _truncate(_normalise(raw))
    notes = list(warnings)
    if cut or clipped:
        notes.append(
            cut_note or f"This is long, so only the first part (about {MAX_TEXT_CHARS:,} characters) was kept."
        )
    if not text and empty_note and not notes:
        notes.append(empty_note)
    elif text and short_note and len(text) < 200:
        notes.append(short_note)
    return Extracted(
        text=text,
        title=_one_line(title, 300) or source[:300],
        kind=kind,
        source=source,
        truncated=cut or clipped,
        warnings=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Files


def extract_file(filename: str, data: bytes) -> Extracted:
    """Read an uploaded PDF, Word (.docx), .txt or .md file and return its text.

    The type must match both the file extension and the file's actual contents.
    """
    name = re.split(r"[\\/]", str(filename or ""))[-1]  # browsers sometimes send a full path
    name = _one_line(name, 10_000)
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in ALLOWED_FILE_TYPES:
        hint = " If it's an older .doc file, open it in Word and save it as .docx first." if ext == ".doc" else ""
        raise ExtractError(f"That file type isn't supported. Upload a PDF, Word (.docx), .txt or .md file.{hint}")
    if len(name) > 200:
        name = stem[: 200 - len(ext)] + ext
    if not data:
        raise ExtractError("That file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ExtractError(f"That file is too big. Files can be up to {MAX_FILE_BYTES // (1024 * 1024)} MB.")

    if ext == ".pdf":
        if b"%PDF-" not in data[:1024]:
            raise ExtractError("This file ends in .pdf but isn't a real PDF. Try exporting it as a PDF again.")
        text, warnings, cut = _read_pdf(data)
        return _finish(text, title=name, kind="pdf", source=name, warnings=warnings, cut=cut)

    if ext == ".docx":
        _check_docx_archive(data)
        return _finish(
            _read_docx(data),
            title=name,
            kind="docx",
            source=name,
            empty_note="No text was found in this Word document.",
        )

    if _looks_binary(data):
        raise ExtractError(
            "This file doesn't look like plain text. If it's a PDF or Word file, upload it with its real extension."
        )
    return _finish(
        _decode_text(data), title=name, kind="text", source=name, empty_note="No text was found in this file."
    )


def _looks_binary(data: bytes) -> bool:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return False
    sample = data[:8192]
    if sample.startswith((b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff", b"\x7fELF")):
        return True
    nuls = sample.count(0)  # a few stray NULs are tolerated (and stripped); lots mean a binary file
    return nuls > 8 and nuls > len(sample) // 10


def _decode_text(data: bytes) -> str:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _read_pdf(data: bytes) -> tuple[str, list[str], bool]:
    """Return the PDF's text, notes for the owner, and whether pages were left unread."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
    except Exception as exc:
        raise ExtractError(_DAMAGED_PDF) from exc

    if encrypted:
        try:
            unlocked = reader.decrypt("") != pypdf.PasswordType.NOT_DECRYPTED
        except Exception as exc:  # e.g. AES encryption needs the optional `cryptography` package
            raise ExtractError(
                "This PDF is password-protected or encrypted in a way we can't open. "
                "Save or print it as a new, unprotected PDF and upload that."
            ) from exc
        if not unlocked:
            raise ExtractError(
                "This PDF is password-protected. Remove the password (or print it to a new PDF) and upload it again."
            )

    try:
        pages = reader.pages
        total = len(pages)
    except Exception as exc:
        raise ExtractError(_DAMAGED_PDF) from exc
    if total == 0:
        raise ExtractError("This PDF has no pages.")

    parts: list[str] = []
    size = attempted = failed = 0
    for index in range(min(total, MAX_PDF_PAGES)):
        if size > 2 * MAX_TEXT_CHARS:
            break
        attempted += 1
        try:
            page_text = pages[index].extract_text() or ""
        except Exception:
            failed += 1
            continue
        parts.append(page_text)
        size += len(page_text)

    if failed == attempted:
        raise ExtractError(_DAMAGED_PDF)
    text = "\n\n".join(parts)
    warnings: list[str] = []
    if failed:
        warnings.append(f"{failed} of {attempted} pages couldn't be read and were skipped.")
    visible = len(re.sub(r"\s+", "", _CONTROL_RE.sub("", text)))
    if visible == 0:
        warnings.append(
            "This PDF looks scanned; no text found. Upload a text-based PDF or a Word file, or paste the text."
        )
    elif visible < 30 * (attempted - failed):
        warnings.append(
            "This PDF looks scanned or is mostly images, so only a little text could be read. "
            "A text-based PDF or a Word file will work better."
        )
    return text, warnings, attempted < total


# Word documents: python-docx opens the package; we walk the XML ourselves because
# python-docx's paragraph text skips content controls (w:sdt) and text boxes, which
# resume templates use a lot.

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W_P, _W_TBL, _W_TR, _W_TC = f"{_W}p", f"{_W}tbl", f"{_W}tr", f"{_W}tc"
_W_T, _W_TAB, _W_BR, _W_CR, _W_HYPHEN = f"{_W}t", f"{_W}tab", f"{_W}br", f"{_W}cr", f"{_W}noBreakHyphen"
_W_TXBX = f"{_W}txbxContent"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_STRUCTURE_SKIP = frozenset(
    {f"{_W}{n}" for n in ("sectPr", "sdtPr", "sdtEndPr", "tblPr", "tblGrid", "trPr", "tcPr", "pPr")} | {_MC_FALLBACK}
)
_INLINE_SKIP = frozenset({f"{_W}{n}" for n in ("pPr", "rPr", "sdtPr", "sdtEndPr", "del")} | {_W_TXBX, _MC_FALLBACK})


def _check_docx_archive(data: bytes) -> None:
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ExtractError(
            "This looks like an older Word (.doc) file renamed to .docx. Open it in Word, save it as .docx, "
            "and upload it again."
        )
    not_word = "This file ends in .docx but isn't a real Word document. Try saving it as .docx again."
    if not data.startswith(b"PK\x03\x04"):
        raise ExtractError(not_word)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except Exception as exc:
        raise ExtractError(not_word) from exc
    if "word/document.xml" not in {info.filename for info in infos}:
        raise ExtractError(not_word)
    if sum(info.file_size for info in infos) > MAX_DOCX_UNPACKED_BYTES:
        raise ExtractError("This Word document is too large to read once unpacked.")


def _read_docx(data: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(data))
        reader = _DocxText()
        blocks: list[str] = []
        seen_headers: set[str] = set()
        for part in document.part.package.iter_parts():
            if isinstance(part, HeaderPart):  # contact details often live in the page header
                header = "\n".join(reader.blocks(part.element)).strip()
                if header and header not in seen_headers:
                    seen_headers.add(header)
                    blocks.extend([header, ""])
        blocks.extend(reader.blocks(document.element.body))
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError(_DAMAGED_DOCX) from exc
    return "\n".join(blocks)


def _children(element: Any, wanted: frozenset[str]) -> Iterator[Any]:
    """Yield children with a wanted tag, looking through wrappers such as content controls."""
    for child in element:
        tag = child.tag
        if not isinstance(tag, str) or tag in _STRUCTURE_SKIP:
            continue
        if tag in wanted:
            yield child
        elif tag not in (_W_P, _W_TBL, _W_TR, _W_TC):
            yield from _children(child, wanted)


class _DocxText:
    """Collect text from Word XML in reading order: paragraphs, tables (cell text) and text boxes."""

    _BLOCKS = frozenset({_W_P, _W_TBL})

    def __init__(self) -> None:
        self._seen_boxes: set[Any] = set()
        self._seen_box_text: set[tuple[str, ...]] = set()

    def blocks(self, element: Any) -> list[str]:
        lines: list[str] = []
        for child in _children(element, self._BLOCKS):
            if child.tag == _W_P:
                parts: list[str] = []
                self._inline(child, parts)
                lines.append("".join(parts))
                lines.extend(self._text_boxes(child))
            else:
                lines.extend(self._table(child))
        return lines

    def _inline(self, element: Any, parts: list[str]) -> None:
        for child in element:
            tag = child.tag
            if not isinstance(tag, str) or tag in _INLINE_SKIP:
                continue
            if tag == _W_T:
                parts.append(child.text or "")
            elif tag == _W_TAB:
                parts.append("\t")
            elif tag in (_W_BR, _W_CR):
                parts.append("\n")
            elif tag == _W_HYPHEN:
                parts.append("-")
            else:
                self._inline(child, parts)

    def _table(self, table: Any) -> list[str]:
        lines = [""]
        for row in _children(table, frozenset({_W_TR})):
            cells: list[list[str]] = []
            for cell in _children(row, frozenset({_W_TC})):
                cell_lines = [s.strip() for block in self.blocks(cell) for s in block.split("\n") if s.strip()]
                if cell_lines:
                    cells.append(cell_lines)
            if all(len(cell) == 1 for cell in cells):
                lines.append(" | ".join(cell[0] for cell in cells))
            else:  # a layout table (common in resumes): keep each cell as its own block
                for cell in cells:
                    lines.extend([*cell, ""])
        lines.append("")
        return lines

    def _text_boxes(self, paragraph: Any) -> list[str]:
        lines: list[str] = []
        for box in paragraph.iter(_W_TXBX):
            if box in self._seen_boxes or any(a.tag == _MC_FALLBACK for a in box.iterancestors()):
                continue
            self._seen_boxes.add(box)
            block = tuple(line for line in self.blocks(box) if line.strip())
            if block and block not in self._seen_box_text:
                self._seen_box_text.add(block)
                lines.extend(["", *block, ""])
        return lines


# ---------------------------------------------------------------------------
# Links: classification and safety checks


_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(?!\d)")
_NUMERIC_HOST_RE = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}", re.IGNORECASE)


def classify_url(url: str) -> str:
    """Say what kind of site a link points to: "linkedin", "github", "x" or "web"."""
    try:
        return _classify(_to_url(url))
    except ExtractError:
        return "web"


def _classify(url: httpx.URL) -> str:
    host = _host(url)
    if host == "linkedin.com" or host.endswith(".linkedin.com") or host == "lnkd.in":
        return "linkedin"
    if host in ("github.com", "www.github.com"):
        return "github"
    if host in ("x.com", "twitter.com") or host.endswith((".x.com", ".twitter.com")):
        return "x"
    return "web"


def _host(url: httpx.URL) -> str:
    return url.raw_host.decode("ascii", "replace").lower().rstrip(".")


def _to_url(raw: str) -> httpx.URL:
    """Parse what the owner typed. A bare "example.com/me" is read as https://example.com/me."""
    text = str(raw or "").strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise ExtractError("That link is too long." if text else "Please enter a web address.")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text):
        raise ExtractError(_INVALID_URL)
    if "://" not in text:
        scheme = _SCHEME_RE.match(text)
        if scheme and scheme.group(1).lower() not in ("http", "https"):
            raise ExtractError("Only http:// and https:// links can be read.")
        text = "https:" + text if text.startswith("//") else "https://" + text
    try:
        return httpx.URL(text)
    except (httpx.InvalidURL, ValueError, TypeError, UnicodeError) as exc:
        raise ExtractError(_INVALID_URL) from exc


def _check_url_parts(url: httpx.URL) -> None:
    """Refuse odd schemes, credentials, non-standard ports and over-long links."""
    if url.scheme not in ("http", "https"):
        raise ExtractError("Only http:// and https:// links can be read.")
    if url.userinfo:
        raise ExtractError("Links with a username or password in them aren't allowed.")
    if not url.raw_host:
        raise ExtractError(_INVALID_URL)
    if url.port is not None and url.port not in (80, 443):
        raise ExtractError("Only links on the standard web ports (80 and 443) can be read.")
    if len(str(url)) > MAX_URL_LENGTH:
        raise ExtractError("That link is too long.")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for anything that isn't an ordinary public internet address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_blocked_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None or ip.teredo is not None:
            return True  # tunnel addresses can smuggle an internal IPv4 address
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Read a host that is an IP address, including odd spellings like 127.1 or 0x7f000001."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST_RE.fullmatch(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError as exc:
            raise ExtractError(_INVALID_URL) from exc
    return None


def _resolve_public(host: str, port: int) -> list[str]:
    """Resolve a host name and return its addresses, refusing if ANY of them is private or local."""
    name = host.lower().rstrip(".")
    if not name:
        raise ExtractError(_INVALID_URL)
    if name == "localhost" or name.endswith(".localhost"):
        raise ExtractError(_PRIVATE_ADDRESS)
    literal = _literal_ip(name)
    if literal is not None:
        if _is_blocked_ip(literal):
            raise ExtractError(_PRIVATE_ADDRESS)
        return [str(literal)]
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise ExtractError(_UNKNOWN_HOST) from exc
    addresses: list[str] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise ExtractError(_PRIVATE_ADDRESS)
        try:
            ip = ipaddress.ip_address(str(sockaddr[0]))
        except ValueError as exc:
            raise ExtractError(_PRIVATE_ADDRESS) from exc
        if _is_blocked_ip(ip):
            raise ExtractError(_PRIVATE_ADDRESS)
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise ExtractError(_UNKNOWN_HOST)
    return addresses


def _vet(url: httpx.URL) -> None:
    """Every hop (first request and each redirect) goes through here before anything is sent."""
    _check_url_parts(url)
    kind = _classify(url)
    if kind == "linkedin":
        raise ExtractError(LINKEDIN_MESSAGE)
    if kind == "x":
        raise ExtractError(X_MESSAGE)
    _resolve_public(_host(url), url.port or (443 if url.scheme == "https" else 80))


class _GuardedBackend(httpcore.SyncBackend):
    """Re-checks the address right before connecting and connects to exactly that address.

    This closes the gap where DNS says "public" during the check and "private" a moment later.
    """

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        last: Exception = httpcore.ConnectError("no usable address")
        for address in _resolve_public(host, port):
            try:
                return super().connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise last


class _GuardedTransport(httpx.HTTPTransport):
    """httpx's normal transport, but every connection goes through _GuardedBackend and no proxy is used."""

    def __init__(self) -> None:
        super().__init__(retries=0)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=10,
            max_keepalive_connections=5,
            keepalive_expiry=5.0,
            network_backend=_GuardedBackend(),
        )


def _default_client() -> httpx.Client:
    return httpx.Client(
        transport=_GuardedTransport(), timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False
    )


# ---------------------------------------------------------------------------
# Links: fetching


@dataclass(frozen=True)
class _Page:
    url: httpx.URL
    status: int
    media_type: str = ""
    charset: str | None = None
    body: bytes = b""
    cut: bool = False  # the body hit MAX_RESPONSE_BYTES and the rest was not read


def extract_url(url: str, *, client: httpx.Client | None = None) -> Extracted:
    """Fetch the single page a link points to and return its readable text.

    LinkedIn and X are refused with advice (they block apps). GitHub profile links are read
    through GitHub's public API. Pass `client` to reuse a connection pool or to test; the
    default client also re-checks addresses at connect time and ignores proxy settings.
    """
    target = _to_url(url)
    _check_url_parts(target)
    kind = _classify(target)
    if kind == "linkedin":
        raise ExtractError(LINKEDIN_MESSAGE)
    if kind == "x":
        raise ExtractError(X_MESSAGE)

    deadline = time.monotonic() + TOTAL_FETCH_SECONDS
    http = client or _default_client()
    try:
        user = _github_user(target) if kind == "github" else None
        if user:
            return _extract_github(http, user, deadline)
        return _extract_web(http, target, deadline)
    finally:
        if client is None:
            http.close()


def _fetch(
    client: httpx.Client,
    url: httpx.URL,
    *,
    deadline: float,
    accept: str,
    allowed_types: frozenset[str],
    extra_headers: dict[str, str] | None = None,
) -> _Page:
    """GET one URL, following up to MAX_REDIRECTS redirects by hand and vetting every hop."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        _vet(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExtractError(_TOO_SLOW)
        headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "gzip, deflate"}
        headers.update(extra_headers or {})
        request = client.build_request("GET", current, headers=headers, timeout=min(FETCH_TIMEOUT_SECONDS, remaining))
        try:
            response = client.send(request, stream=True, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise ExtractError(_TOO_SLOW) from exc
        except httpx.HTTPError as exc:
            raise ExtractError(_CANT_CONNECT) from exc
        try:
            if response.is_redirect:
                current = _redirect_target(current, response.headers["location"])
                continue
            if not response.is_success:
                return _Page(url=current, status=response.status_code)
            media_type, charset = _content_type(response.headers.get("content-type", ""))
            if media_type not in allowed_types:
                raise ExtractError(_wrong_type_message(media_type))
            body, cut = _read_body(response, deadline)
            return _Page(current, response.status_code, media_type, charset, body, cut)
        except httpx.TimeoutException as exc:
            raise ExtractError(_TOO_SLOW) from exc
        except httpx.HTTPError as exc:
            raise ExtractError(_DROPPED) from exc
        finally:
            response.close()
    raise ExtractError("That link redirected too many times.")


def _redirect_target(current: httpx.URL, location: str) -> httpx.URL:
    try:
        target = current.join(location)
    except (httpx.InvalidURL, ValueError, TypeError, UnicodeError) as exc:
        raise ExtractError("That link redirected to an address that isn't valid.") from exc
    _check_url_parts(target)
    return target


def _content_type(header: str) -> tuple[str, str | None]:
    media, _, params = header.partition(";")
    charset = None
    for param in params.split(";"):
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset":
            charset = value.strip().strip("\"'").lower() or None
    return media.strip().lower(), charset


def _wrong_type_message(media_type: str) -> str:
    if not media_type:
        return "That link didn't say what kind of content it is, so it wasn't read."
    if media_type == "application/pdf":
        return "That link points to a PDF. Download it and upload the file here instead."
    if media_type.startswith("image/"):
        return "That link points to an image, not a web page."
    if "word" in media_type or "officedocument" in media_type:
        return "That link points to a document file. Download it and upload the file here instead."
    safe = re.sub(r"[^a-z0-9.+\-/]", "", media_type)[:80]
    return f"That link isn't a web page we can read (it's {safe})."


class _Inflater:
    """Decompress gzip/deflate bodies a bounded amount at a time, so a tiny bomb can't fill memory."""

    def __init__(self, gzip: bool) -> None:
        self._gzip = gzip
        self._first = True
        self._z = zlib.decompressobj(zlib.MAX_WBITS | 16 if gzip else zlib.MAX_WBITS)

    def __call__(self, chunk: bytes, limit: int) -> bytes:
        try:
            data = self._z.decompress(chunk, limit)
        except zlib.error:
            if self._gzip or not self._first:
                raise
            self._z = zlib.decompressobj(-zlib.MAX_WBITS)  # some servers send raw deflate
            data = self._z.decompress(chunk, limit)
        self._first = False
        return data


def _read_body(response: httpx.Response, deadline: float) -> tuple[bytes, bool]:
    """Read at most MAX_RESPONSE_BYTES of (decompressed) body, then stop."""
    coding = response.headers.get("content-encoding", "").strip().lower()
    if coding in ("", "identity"):
        inflate = None
    elif coding in ("gzip", "x-gzip", "deflate"):
        inflate = _Inflater(gzip=coding != "deflate")
    else:
        raise ExtractError("That site sent the page in a compressed format we can't read.")
    body = bytearray()
    try:
        for chunk in response.iter_raw():
            if time.monotonic() > deadline:
                raise ExtractError(_TOO_SLOW)
            room = MAX_RESPONSE_BYTES + 1 - len(body)
            body += inflate(chunk, room) if inflate else chunk[:room]
            if len(body) > MAX_RESPONSE_BYTES:
                return bytes(body[:MAX_RESPONSE_BYTES]), True
    except httpx.StreamConsumed:  # already read into memory (e.g. by a test transport)
        content = response.content
        return content[:MAX_RESPONSE_BYTES], len(content) > MAX_RESPONSE_BYTES
    except zlib.error as exc:
        raise ExtractError("That page couldn't be decoded.") from exc
    return bytes(body), False


def _status_message(status: int) -> str:
    if status in (401, 403):
        return f"That page is private or blocks apps from reading it (HTTP {status}). Paste the text here instead."
    if status in (404, 410):
        return f"That page wasn't found (HTTP {status}). Check the link."
    if status == 429:
        return "That site is limiting requests right now. Try again later."
    if status >= 500:
        return f"That site is having problems right now (HTTP {status}). Try again later."
    return f"That page couldn't be loaded (HTTP {status})."


# ---------------------------------------------------------------------------
# Links: web pages


def _extract_web(client: httpx.Client, url: httpx.URL, deadline: float) -> Extracted:
    page = _fetch(client, url, deadline=deadline, accept=_PAGE_ACCEPT, allowed_types=ALLOWED_PAGE_TYPES)
    if not 200 <= page.status < 300:
        raise ExtractError(_status_message(page.status))
    if page.media_type == "text/plain":
        text = page.body.decode(_codec(page.charset) or "utf-8", errors="replace")
        title = _url_title(page.url)
    else:
        markup = _decode_html(page.body, page.charset)
        text = _html_to_text(markup, str(page.url))
        title = _html_title(markup) or _url_title(page.url)
    return _finish(
        text,
        title=title,
        kind="web",
        source=str(page.url),
        cut=page.cut,
        cut_note="That page is very large, so only the first part of it was read.",
        empty_note="No readable text was found on that page. If it needs JavaScript to load, paste the text instead.",
        short_note="Only a little text was found on that page. If parts of it need JavaScript, paste those instead.",
    )


def _codec(name: str | None) -> str | None:
    if not name:
        return None
    try:
        info = codecs.lookup(name)
    except LookupError:
        return None
    return info.name if getattr(info, "_is_text_encoding", True) else None


_META_CHARSET_RE = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9_\-:.]+)", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)


def _decode_html(body: bytes, charset: str | None) -> str:
    encoding = _codec(charset)
    if encoding is None:
        match = _META_CHARSET_RE.search(body[:4096])
        encoding = _codec(match.group(1).decode("ascii")) if match else None
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _html_to_text(markup: str, url: str) -> str:
    """Main-content extraction with trafilatura, falling back to plain tag stripping."""
    options: dict[str, Any] = {"url": url, "include_comments": False, "include_tables": True}
    try:
        text = trafilatura.extract(markup, favor_precision=True, **options)
        if not text or len(text.strip()) < _SHORT_PAGE_CHARS:
            # Precision mode can drop most of a short personal homepage (even the owner's name).
            fuller = trafilatura.extract(markup, favor_recall=True, **options)
            if fuller and len(fuller) > len(text or ""):
                text = fuller
    except Exception:
        text = None
    if not text or not text.strip():
        text = _strip_tags(markup)
    return text


def _html_title(markup: str) -> str | None:
    try:
        meta = trafilatura.extract_metadata(markup)
        if meta is not None and meta.title and _one_line(meta.title, 300):
            return _one_line(meta.title, 300)
    except Exception:
        pass
    match = _TITLE_RE.search(markup)
    return (_one_line(html.unescape(match.group(1)), 300) or None) if match else None


def _url_title(url: httpx.URL) -> str:
    segments = [s for s in url.path.split("/") if s]
    return _one_line(segments[-1] if segments else _host(url), 300) or _host(url)


class _TagStripper(HTMLParser):
    """Keep visible text only: skip scripts, styles and similar, and break lines at block elements."""

    _SKIP = frozenset({"script", "style", "noscript", "template", "svg", "math", "title", "iframe", "object"})
    _BLOCK = frozenset(
        "address article aside blockquote br dd div dl dt figcaption figure footer form h1 h2 h3 h4 h5 h6 "
        "header hr li main nav ol p pre section table td th tr ul".split()
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _strip_tags(markup: str) -> str:
    parser = _TagStripper()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        pass
    return "".join(parser.parts)


# ---------------------------------------------------------------------------
# Links: GitHub profiles


_GITHUB_USER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_GITHUB_RESERVED = frozenset(
    "about account apps blog codespaces collections contact copilot customer-stories dashboard discussions "
    "education enterprise events explore features home issues join login logout marketplace mobile new "
    "notifications orgs organizations partners pricing pulls readme resources search security settings signup "
    "site solutions sponsors stars team topics trending watching".split()
)
_JSON_TYPES = frozenset({"application/json"})
_GITHUB_HEADERS = {"X-GitHub-Api-Version": "2022-11-28"}


def _github_user(url: httpx.URL) -> str | None:
    """The username for a profile link like github.com/<user>; None for repos and other pages."""
    segments = [s for s in url.path.split("/") if s]
    if len(segments) != 1:
        return None
    name = segments[0]
    if name.lower() in _GITHUB_RESERVED or not _GITHUB_USER_RE.fullmatch(name):
        return None
    return name


def _github_json(client: httpx.Client, path: str, deadline: float, params: dict[str, str] | None = None) -> _Page:
    url = httpx.URL(f"https://api.github.com{path}", params=params)
    return _fetch(
        client,
        url,
        deadline=deadline,
        accept="application/vnd.github+json",
        allowed_types=_JSON_TYPES,
        extra_headers=_GITHUB_HEADERS,
    )


def _parse_json(page: _Page) -> Any:
    try:
        return json.loads(page.body)
    except ValueError as exc:
        raise ExtractError("GitHub sent an unexpected reply. Try again later.") from exc


def _extract_github(client: httpx.Client, user: str, deadline: float) -> Extracted:
    page = _github_json(client, f"/users/{user}", deadline)
    if page.status == 404:
        raise ExtractError(f"There's no GitHub account called “{user}”. Check the link.")
    if page.status in (403, 429):
        raise ExtractError(_GITHUB_BUSY)
    if not 200 <= page.status < 300:
        raise ExtractError(f"GitHub didn't respond properly (HTTP {page.status}). Try again later.")
    profile = _parse_json(page)
    if not isinstance(profile, dict):
        raise ExtractError("GitHub sent an unexpected reply. Try again later.")

    warnings: list[str] = []
    repos: list[dict[str, Any]] = []
    repos_failed = "Your GitHub repositories couldn't be loaded right now, so only the profile was read."
    try:
        repo_page = _github_json(
            client, f"/users/{user}/repos", deadline, params={"sort": "pushed", "per_page": "10", "type": "owner"}
        )
        listed = _parse_json(repo_page) if repo_page.status == 200 else None
        if isinstance(listed, list):
            repos = [r for r in listed if isinstance(r, dict) and not r.get("fork")][:5]
        else:
            warnings.append(repos_failed)
    except ExtractError:
        warnings.append(repos_failed)

    login = _one_line(profile.get("login") or user, 100)
    name = _one_line(profile.get("name") or "", 200)
    text = _format_github(profile, repos, login, name)
    html_url = str(profile.get("html_url") or "")
    source = html_url if html_url.startswith("https://github.com/") and len(html_url) < 300 else ""
    return _finish(
        text,
        title=f"{name} (@{login}) on GitHub" if name else f"@{login} on GitHub",
        kind="github",
        source=source or f"https://github.com/{login}",
        warnings=warnings,
    )


def _format_github(profile: dict[str, Any], repos: list[dict[str, Any]], login: str, name: str) -> str:
    """Readable plain text from the GitHub API's profile and repository data."""

    def field(key: str, limit: int = 400) -> str:
        value = profile.get(key)
        return _one_line(value, limit) if isinstance(value, (str, int)) and value != "" else ""

    lines = [f"GitHub profile: {name} (@{login})" if name else f"GitHub profile: @{login}"]
    if profile.get("type") == "Organization":
        lines.append("Account type: organization")
    for label, key in (("Bio", "bio"), ("Company", "company"), ("Location", "location"), ("Website", "blog")):
        if value := field(key):
            lines.append(f"{label}: {value}")
    if handle := field("twitter_username", 50):
        lines.append(f"X (Twitter): @{handle}")
    counts = [
        f"{field(k)} {label}" for k, label in (("public_repos", "public repositories"), ("followers", "followers"))
    ]
    if counted := [c for c in counts if c[0].isdigit()]:
        lines.append("; ".join(counted).capitalize())
    if (created := field("created_at", 4)).isdigit():
        lines.append(f"On GitHub since {created}")
    if repos:
        lines.extend(["", "Recently active repositories:"])
        for repo in repos:
            repo_name = _one_line(repo.get("name") or "", 100)
            if not repo_name:
                continue
            details = [_one_line(repo["language"], 40)] if isinstance(repo.get("language"), str) else []
            stars = repo.get("stargazers_count")
            if isinstance(stars, int) and stars > 0:
                details.append(f"{stars} star" + ("s" if stars != 1 else ""))
            line = f"- {repo_name}"
            if isinstance(repo.get("description"), str) and (description := _one_line(repo["description"], 300)):
                line += f": {description}"
            if details:
                line += f" ({', '.join(details)})"
            lines.append(line)
    return "\n".join(lines)
