#!/usr/bin/env python3
"""Fail a pull request whose body has no Before image and no After image (issue #662, AGENTS.md section 4).

Every PR shows the behaviour on `main` (before) and on the branch (after) as images embedded in the PR body.
Backend-only work shows a rendered screenshot of the real behaviour (see scripts/render_evidence.py), not
a text block. Only Dependabot is exempt.

Standard library only. Environment: PR_BODY (the pull request body, from the event payload), PR_AUTHOR (the PR
author's login), REPO (default BeastxD7/superaide). Exit 0 = ok, 1 = missing images. GitHub annotations and a
markdown summary go to stdout; the caller may append the same text to $GITHUB_STEP_SUMMARY.

An image is a markdown image or an <img> tag. It counts as "before" or "after" by the first of these that
names exactly one of the two: the table column header, the table row label, a "Before:" / "After:" line or heading
above it, its alt text, its file name. Only images hosted on this repository's `evidence` branch or on GitHub
attachments count (a link that may vanish or that nobody can open is not evidence).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

EXEMPT_AUTHORS = {"dependabot[bot]", "dependabot"}
DEFAULT_REPO = "BeastxD7/superaide"

MD_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\(\s*<?(?P<url>[^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_IMAGE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ATTR = re.compile(r"""(?P<name>src|alt)\s*=\s*(?:"(?P<d>[^"]*)"|'(?P<s>[^']*)')""", re.IGNORECASE)
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.DOTALL | re.MULTILINE)
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<t>.*?)\s*#*\s*$")
BEFORE = re.compile(r"(?<![a-z])before(?![a-z])", re.IGNORECASE)
AFTER = re.compile(r"(?<![a-z])after(?![a-z])", re.IGNORECASE)
LABEL_LINE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*|__)?(?P<t>(?:before|after)\b[^:\n]{0,60}?)(?:\*\*|__)?\s*:", re.IGNORECASE)


def klass(text: str) -> str | None:
    """'before' or 'after' when the text names exactly one of them."""
    b, a = bool(BEFORE.search(text)), bool(AFTER.search(text))
    if b and not a:
        return "before"
    if a and not b:
        return "after"
    return None


def accepted_host(url: str, repo: str) -> bool:
    u = url.strip()
    if u.startswith(f"https://raw.githubusercontent.com/{repo}/evidence/"):
        return True
    if u.startswith("https://github.com/user-attachments/"):
        return True
    if re.match(r"^https://(private-)?user-images\.githubusercontent\.com/", u):
        return True
    return u.startswith(f"https://github.com/{repo}/assets/")


@dataclass
class Image:
    alt: str
    url: str
    cls: str | None = None
    ok_host: bool = True


@dataclass
class Result:
    exempt: bool = False
    before: list[Image] = field(default_factory=list)
    after: list[Image] = field(default_factory=list)
    unclassified: list[Image] = field(default_factory=list)
    bad_host: list[Image] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exempt or (bool(self.before) and bool(self.after))


def clean(body: str) -> str:
    return FENCE.sub("", COMMENT.sub("", body.replace("\r\n", "\n")))


def images_in(text: str) -> list[Image]:
    out: list[Image] = []
    for m in MD_IMAGE.finditer(text):
        out.append(Image(m.group("alt"), m.group("url")))
    for m in HTML_IMAGE.finditer(text):
        attrs = {a.group("name").lower(): a.group("d") or a.group("s") or "" for a in ATTR.finditer(m.group(0))}
        if attrs.get("src"):
            out.append(Image(attrs.get("alt", ""), attrs["src"]))
    return out


def basename(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def split_row(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def is_separator(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", line)) and "|" in line


def evaluate(body: str, author: str = "", repo: str = DEFAULT_REPO) -> Result:
    res = Result(exempt=author.strip().lower() in EXEMPT_AUTHORS)
    lines = clean(body or "").split("\n")
    context: str | None = None  # the nearest "Before" / "After" heading or label line above
    header: list[str] | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        h = HEADING.match(line)
        if h:
            context = klass(h.group("t"))
            header = None
        else:
            lab = LABEL_LINE.match(line)
            if lab:
                context = klass(lab.group("t"))
        in_table = "|" in line
        if in_table and i + 1 < len(lines) and is_separator(lines[i + 1]) and not is_separator(line):
            header = split_row(line)
            i += 2
            continue
        if not in_table:
            header = None
        cells = split_row(line) if in_table else [line]
        for col, cell in enumerate(cells):
            for img in images_in(cell):
                signals = [
                    klass(header[col]) if in_table and header and col < len(header) else None,
                    klass(cells[0]) if in_table and col > 0 else None,
                    context,
                    klass(img.alt),
                    klass(basename(img.url)),
                ]
                img.cls = next((s for s in signals if s), None)
                img.ok_host = accepted_host(img.url, repo)
                if not img.ok_host:
                    res.bad_host.append(img)
                elif img.cls == "before":
                    res.before.append(img)
                elif img.cls == "after":
                    res.after.append(img)
                else:
                    res.unclassified.append(img)
        i += 1
    return res


TEMPLATE = """## Before and after

### Before (main)

![before: <what the reader sees on main>](https://raw.githubusercontent.com/{repo}/evidence/pr-<n>/before-<name>.png)

### After (this branch)

![after: <what the reader sees on the branch>](https://raw.githubusercontent.com/{repo}/evidence/pr-<n>/after-<name>.png)"""


def message(res: Result, repo: str) -> list[str]:
    """What is wrong and exactly what to add. Empty when the body passes."""
    if res.ok:
        return []
    missing = [n for n, got in (("Before", res.before), ("After", res.after)) if not got]
    lines = [
        f"This pull request body has no {' and no '.join(missing)} image. Every PR must show the behaviour on "
        "main (Before) and on this branch (After) as images embedded in the body (AGENTS.md section 4, issue #662).",
        "",
        "Found: "
        f"{len(res.before)} before image(s), {len(res.after)} after image(s)"
        + (f", {len(res.unclassified)} image(s) not under a Before or After label" if res.unclassified else "")
        + (f", {len(res.bad_host)} image(s) on a host that does not count" if res.bad_host else "")
        + ".",
    ]
    if res.bad_host:
        shown = ", ".join(sorted({i.url for i in res.bad_host})[:3])
        lines.append(
            f"Images that do not count (not on the evidence branch or GitHub attachments): {shown}. "
            f"Publish them on the evidence branch (https://raw.githubusercontent.com/{repo}/evidence/pr-<n>/<file>.png) "
            "or drag them into the PR description so GitHub hosts them."
        )
    if res.unclassified:
        lines.append(
            "Images that sit under no Before or After heading, table column or alt text are not counted: put each "
            "under a '### Before' or '### After' heading (or a Before | After table)."
        )
    lines += [
        "",
        "Add this to the PR description (replace the placeholders), then save it: the check re-runs when the "
        "body is edited, no push needed.",
        "",
        *TEMPLATE.format(repo=repo).split("\n"),
        "",
        "Backend-only change: render the real behaviour (the failing-then-passing test output, the API response, the "
        "chat showing it) as an image with `python3 scripts/render_evidence.py` and publish it the same way. "
        "Text or a code block is not enough. Only Dependabot PRs are exempt.",
    ]
    return lines


def main() -> int:
    repo = os.environ.get("REPO") or DEFAULT_REPO
    author = os.environ.get("PR_AUTHOR", "")
    res = evaluate(os.environ.get("PR_BODY", ""), author, repo)
    if res.exempt:
        print(f"Author {author!r} is exempt (Dependabot): no before and after images required.")
        return 0
    if res.ok:
        print(f"ok: {len(res.before)} before image(s) and {len(res.after)} after image(s).")
        return 0
    msg = message(res, repo)
    print(f"::error title=Before and after images missing::{msg[0]}")
    print("\n".join(msg[1:]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
