"""Introducing yourself over HTTP: files, links, documents and the video call, when the input is bad.

No test touches the network: links go to a fake web (recorded, so a refused link
can be shown to have sent nothing) and the link reader's DNS lookups use a fake table.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import docx
import httpx
import pytest
from sqlalchemy import func, select

from superaide.memory.knowledge import extract
from superaide.platform.db.models import Document, Event, MemoryItem
from superaide.platform.db.session import session_scope
from tests.memory.knowledge.test_extract import PUBLIC_IP, encrypt_pdf, html_response, make_pdf, save_docx

DNS = {
    "example.com": [PUBLIC_IP],
    "internal.example": ["10.1.2.3"],
    "mixed.example": [PUBLIC_IP, "192.168.1.10"],
    "mapped.example": ["::ffff:10.0.0.1"],
    "metadata.example": ["169.254.169.254"],
}


class _ExtractorSocket:
    """The real socket module, except that lookups come from DNS above.

    Patched into the extractor only (not globally, as test_extract does), so the
    database connections these HTTP tests need still resolve "localhost".
    """

    def __getattr__(self, name: str):
        return getattr(socket, name)

    @staticmethod
    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002 - mirrors socket's signature
        if host not in DNS:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (a, port, 0, 0))
            if ":" in a
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
            for a in DNS[host]
        ]


def upload(client, name: str, data: bytes, content_type: str = "application/octet-stream"):
    return client.post("/app/intro/files", files={"file": (name, data, content_type)})


def share_link(client, url):
    return client.post("/app/intro/links", json={"url": url})


def _documents() -> list[Document]:
    with session_scope() as s:
        return list(s.scalars(select(Document)))


def _count(model, *where) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


@dataclass
class FakeWeb:
    pages: dict[str, httpx.Response | Callable[[httpx.Request], httpx.Response]] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        page = self.pages.get(str(request.url))
        if callable(page):
            return page(request)
        return page if page is not None else httpx.Response(404)


@pytest.fixture(autouse=True)
def web(monkeypatch) -> FakeWeb:
    """The link reader's HTTP client and DNS talk to fakes instead of the internet."""
    fake = FakeWeb()
    monkeypatch.setattr(extract, "_default_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handle)))
    monkeypatch.setattr(extract, "socket", _ExtractorSocket())
    return fake


# --- files ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "isn't supported"),
        ("resume.doc", b"\xd0\xcf\x11\xe0" + b"0" * 100, "save it as .docx"),
        ("tool.exe", b"MZ\x90\x00", "isn't supported"),
        ("page.html", b"<p>Hi, I'm Jordan</p>", "isn't supported"),
        ("archive.zip", b"PK\x03\x04", "isn't supported"),
        ("noextension", b"Jordan builds agents", "isn't supported"),
        ("resume.pdf.exe", b"%PDF-1.4", "isn't supported"),
        ("docs/", b"hello", "isn't supported"),
        ("empty.txt", b"", "empty"),
        ("empty.pdf", b"", "empty"),
        ("resume.pdf", b"Jordan builds agents", "isn't a real PDF"),
        ("resume.pdf", b"%PDF-1.4 and then garbage", "couldn't read this PDF"),
        ("resume.docx", b"Jordan builds agents", "isn't a real Word document"),
        ("notes.txt", b"\x00\x01\x02\x03" * 64, "doesn't look like plain text"),
        ("notes.md", make_pdf(["hi"]), "doesn't look like plain text"),
    ],
    ids=lambda value: value if isinstance(value, str) and len(value) < 30 else "data",
)
def test_files_that_cant_be_read_are_refused_with_a_reason(owner, name, data, message):
    before = _count(Event)
    r = upload(owner, name, data)
    assert r.status_code == 422, r.text
    assert message in r.json()["detail"]
    assert _documents() == [] and _count(Event) == before  # nothing kept, nothing said


def test_a_pdf_with_no_text_gets_a_helpful_422(owner):
    r = upload(owner, "scan.pdf", make_pdf([]))
    assert r.status_code == 422 and "looks scanned" in r.json()["detail"]
    assert _documents() == []


def test_a_word_document_with_no_text_gets_a_helpful_422(owner):
    r = upload(owner, "blank.docx", save_docx(docx.Document()))
    assert r.status_code == 422 and "No text was found in this Word document" in r.json()["detail"]
    assert _documents() == []


def test_a_password_protected_pdf_is_refused(owner):
    r = upload(owner, "locked.pdf", encrypt_pdf(make_pdf(["Jordan Whitfield"]), "secret"))
    assert r.status_code == 422 and "password" in r.json()["detail"]


def test_a_text_file_of_only_whitespace_is_refused(owner):
    r = upload(owner, "blank.txt", b" \n\t \r\n")
    assert r.status_code == 422 and "No text was found" in r.json()["detail"]


def test_the_size_limit_is_exact(owner, monkeypatch):
    monkeypatch.setattr(extract, "MAX_FILE_BYTES", 1000)
    r = upload(owner, "big.txt", b"a" * 1001)
    assert r.status_code == 422 and "too big" in r.json()["detail"]
    assert upload(owner, "fits.txt", b"b" * 1000).status_code == 200


def test_a_file_over_the_real_limit_is_refused(owner):
    r = upload(owner, "huge.txt", b"a" * (extract.MAX_FILE_BYTES + 1))
    assert r.status_code == 422 and "too big" in r.json()["detail"]
    assert _documents() == []


@pytest.mark.parametrize(
    ("name", "kept_as"),
    [
        ("../../etc/passwd.txt", "passwd.txt"),
        ("..\\..\\Windows\\win.ini.txt", "win.ini.txt"),
        ("/etc/cron.d/evil.md", "evil.md"),
        ("C:\\Users\\jordan\\Desktop\\cv.txt", "cv.txt"),
        ("a/b/../../c.txt", "c.txt"),
        ("\x1b[31mred\x1b[0m.txt", "[31mred[0m.txt"),
    ],
)
def test_file_names_with_paths_or_control_characters_are_reduced_to_a_plain_name(owner, name, kept_as):
    r = upload(owner, name, f"Notes from {kept_as}: Jordan builds agents.".encode())
    assert r.status_code == 200, r.text
    (doc,) = _documents()
    assert doc.title == doc.source == kept_as
    assert "/" not in doc.source and "\\" not in doc.source and ".." not in doc.source.replace(".txt", "")


def test_a_long_file_name_is_shortened(owner):
    r = upload(owner, "a" * 300 + ".txt", b"Jordan builds agents.")
    assert r.status_code == 200
    (doc,) = _documents()
    assert len(doc.title) == 200 and doc.title.endswith(".txt")


def test_an_absurdly_long_file_name_is_refused_by_the_form_parser(owner):
    r = upload(owner, "a" * 5000 + ".txt", b"Jordan builds agents.")
    assert r.status_code == 400  # the multipart parser's own limit, before our code runs
    assert _documents() == []


def test_nul_bytes_in_a_text_file_are_dropped_not_a_server_error(owner):
    r = upload(owner, "notes.txt", b"Jordan\x00 builds\x00 agents.")
    assert r.status_code == 200, r.text
    (doc,) = _documents()
    assert "\x00" not in doc.text and "builds" in doc.text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"json": {"file": "Jordan builds agents"}},
        {"files": {"upload": ("cv.txt", b"Jordan", "text/plain")}},
        {"data": {"file": "cv.txt"}},
        {},
    ],
    ids=["json", "wrong-field", "form-text", "nothing"],
)
def test_upload_requests_without_a_file_are_refused(owner, kwargs):
    assert owner.post("/app/intro/files", **kwargs).status_code == 422
    assert _documents() == []


def test_the_same_file_twice_is_kept_once(owner):
    assert upload(owner, "cv.txt", b"Jordan builds agents.").json()["duplicate"] is False
    again = upload(owner, "cv.txt", b"Jordan builds agents.")
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len(_documents()) == 1


def test_a_removed_file_can_be_shared_again_offline(owner):
    first = upload(owner, "cv.txt", b"Jordan builds agents.").json()
    assert owner.post(f"/app/intro/documents/{first['document_id']}/remove").status_code == 200
    again = upload(owner, "cv.txt", b"Jordan builds agents.")
    assert again.status_code == 200 and again.json()["duplicate"] is False
    assert [d["title"] for d in owner.get("/app/intro/documents").json()] == ["cv.txt"]


def test_a_removed_file_can_be_shared_again_once_facts_were_read_from_it(owner, brain):
    """The facts from the first copy are retired but still point at it; sharing it again used to 500."""
    first = upload(owner, "cv.txt", b"Jordan builds agents at Larkfield.").json()
    assert first["facts"] == 2
    assert owner.post(f"/app/intro/documents/{first['document_id']}/remove").json()["facts_removed"] == 2
    again = upload(owner, "cv.txt", b"Jordan builds agents at Larkfield.")
    assert again.status_code == 200, again.text
    assert again.json()["duplicate"] is False and again.json()["facts"] == 2
    docs = owner.get("/app/intro/documents").json()
    assert [(d["title"], d["facts"]) for d in docs] == [("cv.txt", 2)]
    # Only the new copy's facts are live.
    with session_scope() as s:
        live = s.scalars(select(MemoryItem.document_id).where(MemoryItem.valid_to.is_(None))).all()
    assert set(live) == {uuid.UUID(again.json()["document_id"])}


# --- links -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/cv.pdf",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<p>hi</p>",
        "gopher://example.com/",
        "mailto:jordan@example.com",
        "ssh://example.com",
        "ws://example.com/socket",
    ],
)
def test_links_that_arent_http_are_refused_without_a_request(owner, web, url):
    r = share_link(owner, url)
    assert r.status_code == 422 and "Only http" in r.json()["detail"], r.text
    assert web.seen == [] and _documents() == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.1/",
        "http://2130706433/",
        "http://0x7f.0.0.1/",
        "http://10.0.0.1/admin",
        "http://172.16.5.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://localhost/",
        "http://api.localhost/",
        "localhost",
    ],
)
def test_private_loopback_and_link_local_addresses_are_refused(owner, web, url):
    r = share_link(owner, url)
    assert r.status_code == 422 and "private or local" in r.json()["detail"], r.text
    assert web.seen == []


@pytest.mark.parametrize("host", ["internal.example", "mixed.example", "mapped.example", "metadata.example"])
def test_names_that_resolve_to_private_addresses_are_refused(owner, web, host):
    r = share_link(owner, f"https://{host}/about")
    assert r.status_code == 422 and "private or local" in r.json()["detail"]
    assert web.seen == []


@pytest.mark.parametrize(
    "url", ["http://example.com:8080/", "https://example.com:8443/me", "example.com:22", "http://example.com:0/"]
)
def test_non_standard_ports_are_refused(owner, web, url):
    r = share_link(owner, url)
    assert r.status_code == 422, r.text
    assert web.seen == []


@pytest.mark.parametrize(
    ("url", "advice"),
    [
        ("https://www.linkedin.com/in/someone", "Save to PDF"),
        ("linkedin.com/in/someone", "Save to PDF"),
        ("https://in.linkedin.com/in/someone/", "Save to PDF"),
        ("https://lnkd.in/abc123", "Save to PDF"),
        ("https://x.com/someone", "Paste your bio"),
        ("https://twitter.com/someone", "Paste your bio"),
        ("https://mobile.twitter.com/someone", "Paste your bio"),
    ],
)
def test_linkedin_and_x_are_refused_with_guidance(owner, web, url, advice):
    r = share_link(owner, url)
    assert r.status_code == 422 and advice in r.json()["detail"]
    assert web.seen == []


def test_links_with_credentials_are_refused(owner, web):
    r = share_link(owner, "https://me:hunter2@example.com/")
    assert r.status_code == 422 and "username or password" in r.json()["detail"]
    assert "hunter2" not in r.text and web.seen == []


def test_a_redirect_to_a_private_address_is_refused_after_one_request(owner, web):
    web.pages["https://example.com/me"] = httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    r = share_link(owner, "https://example.com/me")
    assert r.status_code == 422 and "private or local" in r.json()["detail"]
    assert web.seen == ["https://example.com/me"]


def test_an_oversized_page_is_cut_and_says_so(owner, web):
    big = ("Jordan builds agents. " * 150_000).encode()  # 3.6 MB, over the 2 MB response limit
    web.pages["https://example.com/big"] = httpx.Response(200, headers={"Content-Type": "text/plain"}, content=big)
    r = share_link(owner, "https://example.com/big")
    assert r.status_code == 200, r.text
    assert any("very large" in w for w in r.json()["warnings"])
    (doc,) = _documents()
    assert len(doc.text) <= extract.MAX_TEXT_CHARS


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "wasn't found"),
        (httpx.Response(403), "private or blocks apps"),
        (httpx.Response(500), "having problems"),
        (httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"%PDF-1.4"), "points to a PDF"),
        (httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"\x89PNG"), "an image"),
        (httpx.Response(200, content=b"no content type"), "didn't say what kind"),
        (html_response("<html><body><script>render()</script></body></html>"), "JavaScript"),
    ],
    ids=["404", "403", "500", "pdf", "image", "no-type", "js-only"],
)
def test_pages_that_cant_be_read_get_a_plain_422(owner, web, response, message):
    web.pages["https://example.com/me"] = response
    r = share_link(owner, "https://example.com/me")
    assert r.status_code == 422 and message in r.json()["detail"], r.text
    assert _documents() == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"url": None},
        {"url": 42},
        {"url": ["https://example.com"]},
        {"url": "a.b"},
        pytest.param({"url": "https://example.com/" + "a" * 2100}, id="too-long"),
    ],
)
def test_malformed_link_requests_are_refused(owner, web, body):
    r = owner.post("/app/intro/links", json=body)
    assert r.status_code == 422
    assert web.seen == []


@pytest.mark.parametrize("url", ["https://exa mple.com/", "https://example.com/\nme", "   ", "https://"])
def test_links_that_arent_addresses_are_refused(owner, web, url):
    r = share_link(owner, url)
    assert r.status_code == 422, r.text
    assert web.seen == []


def test_a_readable_page_is_kept(owner, web):
    """The positive case for the same fake web, so the refusals above are meaningful."""
    web.pages["https://example.com/me"] = html_response(
        "<html><head><title>Jordan</title></head><body><p>Jordan builds AI agents in Singapore.</p></body></html>"
    )
    r = share_link(owner, "  https://example.com/me  ")
    assert r.status_code == 200, r.text
    (doc,) = _documents()
    assert doc.source == "https://example.com/me" and "AI agents" in doc.text


# --- the video call ----------------------------------------------------------------------------


@pytest.mark.parametrize("question_id", ["q99", "q0", "Q1", "q1 ", "", "../q1", "q-1"])
def test_unknown_call_questions_are_refused(owner, question_id):
    r = owner.post("/app/intro/call/answer", json={"question_id": question_id, "transcript": "hello"})
    assert r.status_code == 422 and r.json()["detail"] == "unknown question"
    assert _count(MemoryItem) == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"transcript": "hi"},
        {"question_id": 1, "transcript": "hi"},
        {"question_id": None},
        {"question_id": "q1", "transcript": None},
        {"question_id": "q1", "transcript": ["a"]},
        {"question_id": "q" * 11, "transcript": "hi"},
        pytest.param({"question_id": "q1", "transcript": "x" * 8001}, id="transcript-8001"),
    ],
)
def test_malformed_call_answers_are_refused(owner, body):
    assert owner.post("/app/intro/call/answer", json=body).status_code == 422
    assert _count(MemoryItem) == 0


def test_a_long_transcript_is_accepted_and_bounded(owner):
    r = owner.post("/app/intro/call/answer", json={"question_id": "q1", "transcript": "word " * 1600})  # 8000 chars
    assert r.status_code == 200 and r.json()["next"]["id"] == "q2"
    with session_scope() as s:
        (fact,) = s.scalars(select(MemoryItem.content)).all()
    assert len(fact) <= 4200


def test_an_empty_transcript_moves_on_and_stores_nothing(owner):
    for transcript in ("", "   \n\t  "):
        r = owner.post("/app/intro/call/answer", json={"question_id": "q2", "transcript": transcript})
        assert r.status_code == 200 and r.json()["next"]["id"] == "q3"
    assert _count(MemoryItem) == 0


def test_the_last_question_has_no_next(owner):
    r = owner.post("/app/intro/call/answer", json={"question_id": "q4", "transcript": "Never share my number."})
    assert r.status_code == 200 and r.json() == {"next": None, "reply": None}


def test_finishing_the_call_twice_or_without_answers_is_harmless(owner):
    for _ in range(2):
        assert owner.post("/app/intro/call/finish").status_code == 200
    assert _count(MemoryItem) == 0  # finishing learns nothing by itself
    closing = [m for m in owner.get("/app/messages").json() if "Thanks for the call" in m["text"]]
    assert len(closing) == 2 and "0 things" in closing[0]["text"]


def test_finishing_the_call_needs_no_body_and_ignores_one(owner):
    assert owner.post("/app/intro/call/finish", json={"anything": "at all"}).status_code == 200


# --- chat and the conversation ------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", [{}, {"text": ""}, {"text": None}, {"text": 5}, pytest.param({"text": "x" * 4001}, id="4001-chars")]
)
def test_malformed_chat_messages_are_refused(owner, body):
    assert owner.post("/app/chat", json=body).status_code == 422


@pytest.mark.parametrize("limit", ["-5", "0", "100000", "abc", "1.5", "9" * 5000])
def test_message_history_limit_is_validated_or_clamped(owner, limit):
    r = owner.get("/app/messages", params={"limit": limit})
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200:
        assert 1 <= len(r.json()) <= 500


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/app/chat", {"text": "hello\x00"}),
        ("/app/intro/call/answer", {"question_id": "q1", "transcript": "I lead\x00 AI work"}),
        ("/app/intro/links", {"url": "https://example.com/\x00"}),
    ],
    ids=["chat", "call", "link"],
)
def test_a_nul_character_is_refused_not_a_server_error(owner, web, path, body):
    r = owner.post(path, json=body)
    assert r.status_code == 422 and "NUL" in r.json()["detail"], r.text
    assert _count(MemoryItem) == 0 and web.seen == []


def test_a_chat_message_of_only_whitespace_is_not_a_server_error(owner):
    r = owner.post("/app/chat", json={"text": "  \n\t "})
    assert r.status_code in (200, 422), r.text
