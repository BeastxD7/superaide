#!/usr/bin/env python3
"""Keep the project owner's real name out of examples, fixtures and demo data.

Applies a fixed, case-aware mapping over every git-tracked text file (stdlib only, idempotent):

  backend, scripts, .github   Shashank Rao -> Jordan Whitfield   Shashank -> Jordan   shashank@ -> jordan@
  e2e/, frontend/, docs/      Shashank Rao -> Ananya Menon       Shashank -> Ananya   shashank@ -> ananya@

Files and lines in scripts/example_names_allowlist.txt are skipped (the real owner in notes, this mapping).
See docs/design/example-names.md.

Usage:
  python3 scripts/rename_example_names.py            rewrite files in place, print what changed
  python3 scripts/rename_example_names.py --check    change nothing; exit 1 listing any disallowed occurrence

After a rebase conflict on a file that still has the old name: `git checkout --theirs <file>` (or --ours during
a rebase), run this script, `git add` the file and continue.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "scripts" / "example_names_allowlist.txt"

JORDAN = ("Jordan", "Whitfield")
ANANYA = ("Ananya", "Menon")
ANANYA_PREFIXES = ("e2e/", "frontend/", "docs/")

OLD = re.compile(r"shashank", re.IGNORECASE)


def persona(path: str) -> tuple[str, str]:
    return ANANYA if path.startswith(ANANYA_PREFIXES) else JORDAN


def _case(src: str, new: str) -> str:
    if src.isupper():
        return new.upper()
    if src.islower():
        return new.lower()
    return new[0].upper() + new[1:].lower()


def rewrite(text: str, path: str) -> str:
    first, last = persona(path)

    def full(m: re.Match[str]) -> str:
        return _case(m.group(1), first) + m.group(2) + _case(m.group(3), last)

    def initial(m: re.Match[str]) -> str:
        return _case(m.group(1), first) + m.group(2) + (last[0] if m.group(3).isupper() else last[0].lower())

    # "Shashank Rao", "Shashank-Rao", "shashank.rao", "ShashankRao"; then the initial form "Shashank K", "shashank-k"
    text = re.sub(r"(shashank)([ ._-]?)(rao)", full, text, flags=re.IGNORECASE)
    text = re.sub(r"(shashank)([ _-])(K)\b", initial, text, flags=re.IGNORECASE)
    return OLD.sub(lambda m: _case(m.group(0), first), text)


def load_allowlist() -> tuple[set[str], dict[str, list[re.Pattern[str]]]]:
    whole: set[str] = set()
    lines: dict[str, list[re.Pattern[str]]] = {}
    for raw in ALLOWLIST.read_text().splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        path, _, pattern = raw.partition("::")
        if pattern:
            lines.setdefault(path, []).append(re.compile(pattern))
        else:
            whole.add(path)
    return whole, lines


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def process(path: str, whole: set[str], lines: dict[str, list[re.Pattern[str]]], write: bool) -> list[str]:
    """Return 'path:line: text' for each disallowed occurrence found (before rewriting)."""
    if path in whole:
        return []
    file = ROOT / path
    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError, OSError:
        return []
    if not OLD.search(text):
        return []
    patterns = lines.get(path, [])
    found: list[str] = []
    out: list[str] = []
    for n, line in enumerate(text.splitlines(keepends=True), 1):
        if OLD.search(line) and not any(p.search(line) for p in patterns):
            found.append(f"{path}:{n}: {line.strip()[:140]}")
            line = rewrite(line, path)
        out.append(line)
    if write and found:
        file.write_text("".join(out), encoding="utf-8")
    return found


def main(argv: list[str]) -> int:
    check = "--check" in argv
    whole, lines = load_allowlist()
    hits: list[str] = []
    for path in tracked_files():
        hits += process(path, whole, lines, write=not check)
    if check:
        if hits:
            print("The owner's real name is used as an example name. Run `python3 scripts/rename_example_names.py`:")
            print("\n".join(hits))
            return 1
        print("example names ok")
        return 0
    print(f"rewrote {len({h.split(':')[0] for h in hits})} files, {len(hits)} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
