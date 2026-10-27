"""The email signature block (#439) as a pure function: structure, safety, accessibility, snapshot."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from superaide.identity.principal import signature
from superaide.identity.principal.signature import ACCENTS, Signature, body_html, document_html, signature_html

SIG = Signature(
    agent="Ava",
    owner="Ananya T.",
    title="Personal assistant of Ananya T.",
    disclosure="Ava is an AI assistant acting for Ananya T.",
    verify_url="https://example.test/c/Xk3pQ9aB",
    accent=ACCENTS["indigo"],
    avatar_url="https://example.test/brand/ava-96.png",
    mark_url="https://example.test/brand/superaide-mark.png",
    text="--\nRegards,\nAva\n\nAva is an AI assistant acting for Ananya T. https://example.test/c/Xk3pQ9aB",
)


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def parse(html: str) -> Tags:
    p = Tags()
    p.feed(html)
    return p


def luminance(hex_: str) -> float:
    r, g, b = (int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_with_white(hex_: str) -> float:
    return 1.05 / (luminance(hex_) + 0.05)


def test_the_block_has_the_parts_the_issue_asks_for():
    h = signature_html(SIG)
    assert "Ava</div>" in h and "Personal assistant of Ananya T." in h
    assert "Ava is an AI assistant acting for Ananya T." in h
    assert re.search(r'<a href="https://example\.test/c/Xk3pQ9aB"[^>]*>Verify this assistant</a>', h)
    assert "Sent with" in h and "Superaide" in h


def test_table_layout_inline_css_at_most_600px():
    tags = parse(signature_html(SIG)).tags
    names = {t for t, _ in tags}
    assert {"table", "tr", "td", "img", "a"} <= names
    assert not names & {"script", "link", "iframe", "form", "input", "object", "embed", "video", "audio", "svg"}
    assert all(
        "max-width:600px" in (a.get("style") or "") for t, a in tags if t == "table" and a.get("width") == "100%"
    )
    assert all(a.get("role") == "presentation" for t, a in tags if t == "table")  # layout tables, not data tables


def test_images_have_alt_size_and_come_from_the_public_address():
    imgs = [a for t, a in parse(signature_html(SIG)).tags if t == "img"]
    assert len(imgs) == 2
    for a in imgs:
        assert a["src"].startswith("https://example.test/") and a["width"] and a["height"] and "alt" in a
    assert imgs[0]["alt"] == "Ava" and imgs[0]["width"] == "48" and imgs[0]["height"] == "48"


def test_no_tracking_scripts_remote_fonts_or_extra_hosts():
    h = document_html("Hello", SIG)
    hosts = set(re.findall(r"https?://([^/\"'\s)]+)", h))
    assert hosts == {"example.test"}
    for banned in (
        "<script",
        "@import",
        "@font-face",
        "url(",
        "fonts.googleapis",
        "<link",
        "onload",
        "onerror",
        "javascript:",
    ):
        assert banned not in h.lower()
    assert not any(a.get("width") == "1" and a.get("height") == "1" for t, a in parse(h).tags if t == "img")  # no pixel


def test_avatar_and_mark_are_optional():
    h = signature_html(Signature(**{**SIG.__dict__, "avatar_url": "", "mark_url": ""}))
    assert "<img" not in h and "Sent with" not in h and "Verify this assistant" in h


def test_dark_mode_is_handled_and_the_document_declares_both_schemes():
    h = document_html("Hello", SIG)
    assert 'name="color-scheme" content="light dark"' in h and "prefers-color-scheme: dark" in h
    assert 'lang="en"' in h and "<!doctype html>" in h


@pytest.mark.parametrize("name", list(ACCENTS))
def test_every_accent_keeps_the_white_button_text_readable(name):
    assert contrast_with_white(ACCENTS[name]) >= 4.5, name


def test_an_unknown_accent_falls_back_to_the_default():
    assert signature.accent_hex("hotpink") == ACCENTS["indigo"] and signature.accent_hex("teal") == ACCENTS["teal"]


def test_the_message_is_escaped_so_a_model_cannot_inject_markup():
    evil = '<script>alert(1)</script> <img src=x onerror=y> & "quotes"\n\nsecond <b>para</b>'
    h = document_html(evil, SIG)
    assert "<script" not in h.lower() and "onerror=y>" not in h and "&lt;script&gt;" in h
    assert h.count("<p ") == 2 and "&amp;" in h


def test_text_with_line_breaks_keeps_them():
    assert body_html("a\nb") == '<p style="margin:0 0 14px 0">a<br>b</p>'
    assert body_html("") == ""


def test_attribute_values_are_escaped():
    evil = Signature(**{**SIG.__dict__, "verify_url": 'https://example.test/c/x" onclick="y', "agent": 'A"><b>'})
    h = signature_html(evil)
    assert 'onclick="y' not in h.replace("&quot;", "") or "&quot; onclick=&quot;" in h
    assert "<b>" not in h


def test_snapshot_of_the_block():
    h = signature_html(SIG)
    assert re.sub(r"\s+", " ", h).startswith(
        '<table role="presentation" class="sg-rule" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="max-width:600px;margin-top:20px;border-top:1px solid #e5e7eb"><tr>'
    )
    assert len(h) < 3000  # small enough that no client clips it
    assert h.endswith("</td></tr></table></td></tr></table>")


def test_the_brand_images_exist_are_small_and_the_right_size():
    import struct
    from pathlib import Path

    from superaide.identity.principal import card

    public = Path(__file__).resolve().parents[4] / "frontend" / "public"
    for path, side in ((card.AVATAR_PATH, 96), (card.MARK_PATH, 28)):
        data = (public / path.lstrip("/")).read_bytes()
        assert len(data) <= 20_000, path  # under 20 KB each
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = struct.unpack(">II", data[16:24])
        assert (w, h) == (side, side), path  # shown at half size, so they stay sharp on high-density screens
