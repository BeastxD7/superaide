#!/usr/bin/env python3
"""Render text (test output, an API response, a chat transcript) as a PNG screenshot for a PR's Before and After.

Backend-only changes have nothing to click, but the PR body still needs images (AGENTS.md section 4, issue #662).
This turns the real behaviour into a picture: a terminal-style card with a title, the command and its output.

    # the output of a command (run it on main for Before, on your branch for After)
    python3 scripts/render_evidence.py out/before.png --title "BEFORE (main): the new test fails" \\
        --cmd "uv run pytest -q tests/memory/test_x.py"
    # text from a file or stdin
    uv run pytest -q tests/memory/test_x.py 2>&1 | python3 scripts/render_evidence.py out/after.png --title "AFTER"

Then publish the PNGs on the `evidence` branch (pr-<n>/before-*.png, after-*.png) and embed
https://raw.githubusercontent.com/BeastxD7/superaide/evidence/pr-<n>/<file>.png in the PR body.

Standard library plus the Playwright that e2e/ already installs (`cd e2e && npm ci`). Use --html-only to write just
the HTML page. No secrets in the text: the script strips ANSI colours only, it does not redact. Fake names only.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ROOT = Path(__file__).resolve().parents[1]

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ margin: 0; padding: 24px; background: #f4f4f5; font-family: -apple-system, Segoe UI, sans-serif; }}
  .card {{ width: {width}px; background: #0f172a; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 12px #0003; }}
  .bar {{ padding: 10px 16px; background: {accent}; color: #fff; font-weight: 700; font-size: 15px; }}
  pre {{ margin: 0; padding: 16px; color: #e2e8f0; font: 13px/1.45 Menlo, Consolas, monospace; white-space: pre-wrap;
         word-break: break-word; }}
  .cmd {{ color: #7dd3fc; }}
  .fail {{ color: #fca5a5; }}
  .pass {{ color: #86efac; }}
</style></head><body>
<div class="card"><div class="bar">{title}</div><pre>{body}</pre></div>
</body></html>
"""


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def colour(line: str) -> str:
    esc = html.escape(line)
    if re.search(r"\b(FAILED|ERROR|AssertionError|failed|Traceback)\b|^E\s", line):
        return f'<span class="fail">{esc}</span>'
    if re.search(r"\b(passed|PASSED|ok)\b", line):
        return f'<span class="pass">{esc}</span>'
    return esc


def build_html(text: str, title: str, command: str | None, width: int, accent: str) -> str:
    rows = []
    if command:
        rows.append(f'<span class="cmd">$ {html.escape(command)}</span>')
    rows += [colour(line) for line in strip_ansi(text).rstrip("\n").split("\n")]
    return PAGE.format(title=html.escape(title), body="\n".join(rows), width=width, accent=accent)


def screenshot(page: Path, out: Path, width: int, playwright_dir: Path) -> None:
    bin_ = playwright_dir / "node_modules" / ".bin" / "playwright"
    if not bin_.exists():
        raise SystemExit(
            f"Playwright not found at {bin_}. Run `cd e2e && npm ci` (or pass --playwright-dir), "
            "or use --html-only and screenshot the page yourself."
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(bin_), "screenshot", "--full-page", f"--viewport-size={width + 48},60", page.as_uri(), str(out)]
    done = subprocess.run(cmd, cwd=playwright_dir, capture_output=True, text=True)
    if done.returncode != 0 or not out.exists():
        raise SystemExit(f"screenshot failed: {(done.stderr or done.stdout).strip()}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("out", help="output PNG (or .html with --html-only)")
    p.add_argument("--title", default="Output", help="the card's header, e.g. 'BEFORE (main): the test fails'")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--file", help="read the text from this file (default: stdin)")
    src.add_argument("--cmd", help="run this shell command and render its stdout and stderr")
    p.add_argument("--width", type=int, default=1100, help="card width in pixels (default 1100; 360 for a phone shape)")
    p.add_argument("--accent", default=None, help="header colour (default: red for a title with BEFORE, green for AFTER)")
    p.add_argument("--html-only", action="store_true")
    p.add_argument("--playwright-dir", default=os.environ.get("EVIDENCE_PLAYWRIGHT_DIR", str(ROOT / "e2e")))
    a = p.parse_args(argv)

    if a.cmd:
        done = subprocess.run(a.cmd, shell=True, capture_output=True, text=True)
        text = done.stdout + done.stderr
    elif a.file:
        text = Path(a.file).read_text()
    else:
        text = sys.stdin.read()
    if not text.strip():
        raise SystemExit("nothing to render: the text is empty")
    accent = a.accent or ("#b91c1c" if "before" in a.title.lower() else "#15803d" if "after" in a.title.lower() else "#334155")
    page = build_html(text, a.title, a.cmd, a.width, accent)
    out = Path(a.out)
    if a.html_only:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page)
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "evidence.html"
        f.write_text(page)
        screenshot(f, out, a.width, Path(a.playwright_dir))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
