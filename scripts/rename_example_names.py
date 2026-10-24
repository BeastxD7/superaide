#!/usr/bin/env python3
"""Keep real people's and real companies' names out of examples, fixtures and demo data.

Applies a fixed, case-aware mapping over every git-tracked text file (stdlib only, idempotent). People:

  backend, scripts, .github   Shashank Rao -> Jordan Whitfield   Shashank -> Jordan   shashank@ -> jordan@
  e2e/, frontend/, docs/      Shashank Rao -> Ananya Menon       Shashank -> Ananya   shashank@ -> ananya@

Companies (COMPANIES below): the owner's employer and a few real firms that appeared as fixtures are replaced by
fictional ones everywhere (Pebble Road -> Larkfield, Bluehost -> Fernhost, Kotak -> Marlin, Swiggy -> Teahouse, and
"interviewing at Stripe" in the privacy eval -> Quillstone).

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

# (pattern, replacement, only these path prefixes or None for everywhere). Order matters: specific before general.
# The replacement keeps the case of what it replaces (Bluehost -> Fernhost, BLUEHOST -> FERNHOST, bluehost -> fernhost).
COMPANIES: list[tuple[re.Pattern[str], str, tuple[str, ...] | None]] = [
    (re.compile(r"pebble[ _-]?road", re.IGNORECASE), "larkfield", None),
    (re.compile(r"bluehost\.com", re.IGNORECASE), "fernhost.example", None),
    (re.compile(r"bluehost", re.IGNORECASE), "fernhost", None),
    (re.compile(r"kotak mahindra", re.IGNORECASE), "marlin", None),
    (re.compile(r"kotak\.(?:com|example)", re.IGNORECASE), "marlin.example", None),
    (re.compile(r"kotak", re.IGNORECASE), "marlin", None),
    (re.compile(r"swiggy", re.IGNORECASE), "teahouse", None),
    # Stripe is a real payment provider the product may name; only the employer in a fixture is replaced.
    (re.compile(r"\bStripe\b"), "Quillstone", ("backend/evals/scenarios/privacy.yaml",)),
]


def persona(path: str) -> tuple[str, str]:
    return ANANYA if path.startswith(ANANYA_PREFIXES) else JORDAN


def _case(src: str, new: str) -> str:
    if src.isupper():
        return new.upper()
    if src.islower():
        return new.lower()
    return new[0].upper() + new[1:].lower()


def _company(m: re.Match[str], new: str) -> str:
    return new if "." in new else _case(m.group(0).replace(" ", "").replace("_", "").replace("-", ""), new)


def rewrite_companies(text: str, path: str) -> str:
    for pattern, new, scope in COMPANIES:
        if scope is None or path.startswith(scope):
            text = pattern.sub(lambda m, new=new: _company(m, new), text)
    return text


def has_old(text: str, path: str) -> bool:
    if OLD.search(text):
        return True
    return any(p.search(text) for p, _, scope in COMPANIES if scope is None or path.startswith(scope))


def rewrite(text: str, path: str) -> str:
    return rewrite_companies(rewrite_names(text, path), path)


def rewrite_names(text: str, path: str) -> str:
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
    except (UnicodeDecodeError, OSError):
        return []
    if not has_old(text, path):
        return []
    patterns = lines.get(path, [])
    found: list[str] = []
    out: list[str] = []
    for n, line in enumerate(text.splitlines(keepends=True), 1):
        if has_old(line, path) and not any(p.search(line) for p in patterns):
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
            print("A real name (the owner, a real employer or company) is used as an example. Run `python3 scripts/rename_example_names.py`:")
            print("\n".join(hits))
            return 1
        print("example names ok")
        return 0
    print(f"rewrote {len({h.split(':')[0] for h in hits})} files, {len(hits)} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
