"""The email signature block (#439): HTML for the part a person sees, and the plain-text twin.

Pure functions, no database and no network, so the output is unit- and snapshot-testable. The model writes only the
body of an email; this module builds the signature from the owner's settings and the card link, so the model cannot
change or remove it. The HTML is a table layout with inline CSS (plus one small media query for dark mode), at most
600 px wide, with no scripts, no remote fonts, no tracking pixels and no forms. Images are the avatar and the
Superaide mark, each with alt text and fixed width and height, so nothing breaks when a client blocks images.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

# Accents are the only colour an owner may pick. Each has at least 4.5:1 contrast with the white button text.
ACCENTS: dict[str, str] = {
    "indigo": "#4f46e5",
    "teal": "#0f766e",
    "slate": "#334155",
    "rose": "#be123c",
    "amber": "#b45309",
}
DEFAULT_ACCENT = "indigo"
VERIFY_LABEL = "Verify this assistant"
MAX_WIDTH = 600
AVATAR_PX = 48
MARK_W, MARK_H = 14, 14
FONT = "Arial,Helvetica,sans-serif"

# Light-client colours inline; a media query lifts them for dark clients that honour it. Mid-grey text keeps
# readable contrast on either background in clients that ignore the query.
_DARK = (
    "@media (prefers-color-scheme: dark){"
    ".sg-name{color:#f3f4f6!important}.sg-muted{color:#a1a1aa!important}.sg-rule{border-top-color:#3f3f46!important}"
    ".sg-body{color:#e5e7eb!important}}"
)


@dataclass(frozen=True)
class Signature:
    agent: str  # "Ava"
    owner: str  # the first name (and chosen initial) the owner shows
    title: str  # "Personal assistant of Ananya", or the owner's own line
    disclosure: str  # "Ava is an AI assistant acting for Ananya."
    verify_url: str  # the short card link
    accent: str = ACCENTS[DEFAULT_ACCENT]
    avatar_url: str = ""  # empty: no avatar
    mark_url: str = ""  # empty: no "Sent with Superaide" line
    text: str = ""  # the plain-text twin (sign-off, the AI line and the link), appended to the text part


def accent_hex(name: str) -> str:
    return ACCENTS.get(name, ACCENTS[DEFAULT_ACCENT])


def body_html(text: str) -> str:
    """The owner's message as paragraphs. Everything is escaped: a model cannot inject markup."""
    paras = [p for p in (text or "").replace("\r", "").split("\n\n") if p.strip()]
    out = []
    for p in paras:
        lines = "<br>".join(escape(line) for line in p.strip("\n").split("\n"))
        out.append(f'<p style="margin:0 0 14px 0">{lines}</p>')
    return "".join(out)


def signature_html(sig: Signature) -> str:
    """The signature block alone: a thin rule, the avatar on the left, then name, title, the one-line disclosure
    and the verify button, and optionally a small "Sent with Superaide"."""
    avatar = ""
    if sig.avatar_url:
        avatar = (
            f'<td valign="top" width="{AVATAR_PX + 12}" style="width:{AVATAR_PX + 12}px;padding-right:12px">'
            f'<img src="{escape(sig.avatar_url, quote=True)}" width="{AVATAR_PX}" height="{AVATAR_PX}" '
            f'alt="{escape(sig.agent)}" style="display:block;width:{AVATAR_PX}px;height:{AVATAR_PX}px;'
            f'border:0;border-radius:{AVATAR_PX // 2}px"></td>'
        )
    mark = ""
    if sig.mark_url:
        mark = (
            f'<div class="sg-muted" style="margin-top:10px;font-size:11px;line-height:14px;color:#6b7280">Sent with '
            f'<img src="{escape(sig.mark_url, quote=True)}" width="{MARK_W}" height="{MARK_H}" alt="" '
            f'style="vertical-align:middle;border:0"> Superaide</div>'
        )
    return (
        f'<table role="presentation" class="sg-rule" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="max-width:{MAX_WIDTH}px;margin-top:20px;border-top:1px solid #e5e7eb"><tr>'
        f'<td style="padding-top:12px"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f"{avatar}"
        f'<td valign="top" style="font-family:{FONT};font-size:14px;line-height:20px">'
        f'<div class="sg-name" style="font-size:15px;font-weight:bold;color:#111827">{escape(sig.agent)}</div>'
        f'<div class="sg-muted" style="color:#6b7280">{escape(sig.title)}</div>'
        f'<div class="sg-muted" style="margin-top:6px;font-size:12px;line-height:16px;color:#6b7280">'
        f"{escape(sig.disclosure)}</div>"
        f'<a href="{escape(sig.verify_url, quote=True)}" style="display:inline-block;margin-top:8px;padding:6px 12px;'
        f"background-color:{escape(sig.accent)};color:#ffffff;font-size:12px;line-height:16px;font-weight:bold;"
        f'text-decoration:none;border-radius:4px">{VERIFY_LABEL}</a>'
        f"{mark}</td></tr></table></td></tr></table>"
    )


def document_html(text: str, sig: Signature | None) -> str:
    """The whole HTML part: the message, then the signature when there is one."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        f"<style>{_DARK}</style></head>"
        f'<body class="sg-body" style="margin:0;padding:0;font-family:{FONT};font-size:14px;'
        f'line-height:21px;color:#1f2937">'
        f'<div style="max-width:{MAX_WIDTH}px">{body_html(text)}</div>'
        f"{signature_html(sig) if sig else ''}</body></html>"
    )
