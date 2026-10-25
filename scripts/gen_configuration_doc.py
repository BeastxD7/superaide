#!/usr/bin/env python3
"""Generate the variable tables of docs/configuration.md from backend/.env.example.

The prose around the tables lives in docs/configuration.head.md and docs/configuration.tail.md; this script only
writes the part between them. Run `python3 scripts/gen_configuration_doc.py` after editing .env.example, or with
`--check` to fail when docs/configuration.md is out of date.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "backend/.env.example"
HEAD = ROOT / "docs/configuration.head.md"
TAIL = ROOT / "docs/configuration.tail.md"
OUT = ROOT / "docs/configuration.md"
RULE = re.compile(r"^# =+$")
ENTRY = re.compile(r"^#? ?([A-Z][A-Z0-9_]*)=(.*)$")


def sections() -> list[tuple[str, list[tuple[str, str, bool, str]]]]:
    lines = EXAMPLE.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if "END FEATURE FLAGS INDEX" in ln) + 1
    out: list[tuple[str, list[tuple[str, str, bool, str]]]] = []
    title, rows, comment = "Settings", [], []
    i = start
    while i < len(lines):
        ln = lines[i]
        if RULE.match(ln) and i + 2 < len(lines) and RULE.match(lines[i + 2]):
            if rows:
                out.append((title, rows))
            raw = lines[i + 1].lstrip("# ").strip().rstrip(":")
            title, rows, comment = re.split(r" \(|\. |: ", raw)[0], [], []
            i += 3
            continue
        m = ENTRY.match(ln)
        if m:
            text = " ".join(comment)
            secret = bool(re.search(r"\bSecret\b", text))
            rows.append((m.group(1), m.group(2).strip(), secret, text))
            comment = [] if text else comment
        elif ln.startswith("#"):
            body = ln.lstrip("#").strip()
            if body:
                comment.append(body)
        elif not ln.strip():
            comment = [] if rows or comment else comment
        i += 1
    if rows:
        out.append((title, rows))
    return out


def render() -> str:
    parts = []
    for title, rows in sections():
        parts.append(f"### {title}\n")
        parts.append("| Variable | Default | Secret | What it does |\n| --- | --- | --- | --- |")
        for name, value, secret, text in rows:
            text = (text or "Same note as the entry above.").replace("|", "\\|")
            if len(text) > 700:
                text = text[:697].rsplit(" ", 1)[0] + "..."
            default = f"`{value}`" if value else "empty"
            parts.append(f"| `{name}` | {default} | {'yes' if secret else ''} | {text} |")
        parts.append("")
    return "\n".join(parts)


def build() -> str:
    return HEAD.read_text().rstrip() + "\n\n" + render().rstrip() + "\n\n" + TAIL.read_text().lstrip()


if __name__ == "__main__":
    text = build()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text() == text else (print("docs/configuration.md is stale") or 1))
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
