"""Lobster Rescue (#141): read an OpenClaw or Hermes workspace from a folder on this machine.

Two steps, both local and neither calls a model or the network:
1. `audit(folder)` prints what is wrong with the workspace and writes nothing: secrets lying in
   plain files, a gateway reachable beyond the machine, skills that ask for a shell, lines in the
   memory files that try to give Ava orders. Secret values are never printed, only where they are.
2. `cards(folder)` turns the memory files into context cards (imports/cards.py) for an import
   lease (imports/lease.py). Everything lands private to the owner at the lowest rank, tagged
   with the lease, and is erased with it. Skills are listed in the audit and never imported or
   run; a line that looks like an instruction to the assistant, or holds a secret, is left out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from superaide.features.imports.cards import MAX_CARD_TEXT, MAX_CARDS_PER_REQUEST, MAX_TITLE, clean

MAX_FILE_BYTES = 1_000_000  # a bigger file is not a notes file; it is skipped
MAX_FILES = 400
MEMORY_FILES = {"MEMORY.md": "other", "USER.md": "about", "DREAMS.md": "other"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".json5", ".yaml", ".yml", ".toml", ".env", ".ini", ".cfg", ".conf"}

# name, pattern. Only the kind and the place are ever reported.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Anthropic or OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "Gateway or API token set in a config",
        re.compile(
            r"""(?i)\b(?:gateway[_.-]?(?:auth[_.-]?)?token|api[_-]?key|auth[_-]?token|secret)\b["']?\s*[:=]\s*["']?[A-Za-z0-9_\-./+=]{16,}"""
        ),
    ),
)
EXPOSED_BIND = re.compile(r"""(?i)\b(?:bind|host|listen)\w*["']?\s*[:=]\s*["']?(0\.0\.0\.0|::|\*)["']?""")
NO_AUTH = re.compile(r"""(?i)\b(?:auth\w*|require_?auth)["']?\s*[:=]\s*["']?(false|none|off|disabled)["']?""")
SHELL_ASK = re.compile(
    r"""(?ix)(?:allowed[-_]tools\s*:.*\b(?:bash|shell|exec|terminal|run_command)\b
        |\b(?:bash|sh|zsh)\s+-c\b|\bsubprocess\b|\bos\.system\b|\bchild_process\b|\bsudo\b|\brm\s+-rf\b
        |\bcurl\b[^\n|]*\|\s*(?:sh|bash)\b|\bwget\b[^\n|]*\|\s*(?:sh|bash)\b|```(?:bash|sh|shell|zsh)\b)"""
)
# Memory is a poisoning vector: a line that gives orders, hides a link or asks to leak is not a fact.
ORDERS = re.compile(
    r"""(?ix)\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,30}\b(?:instructions?|rules?|prompts?)\b
        |\b(?:always|never|must)\b[^.\n]{0,60}\b(?:send|forward|post|upload|email|exfiltrate)\b[^.\n]{0,80}(?:https?://|@)
        |^\s*(?:system|assistant)\s*:|<\s*/?\s*(?:system|instructions?)\s*>|\byou are now\b"""
)
# Hermes keeps its entries in one file separated by a section sign (public docs; not checked on a real install).
ENTRY_SEPARATOR = "\u00a7"
# Names that the layout detector and the names-only listing know. Documented, not verified (docs/lobster-runbook.md).
KNOWN_NAMES = {
    "MEMORY.md", "USER.md", "DREAMS.md", "SOUL.md", "SKILL.md", "AGENTS.md", "TOOLS.md", "IDENTITY.md",
    "config.yaml", "auth.json", "openclaw.json", ".env", "memory", "memories", "skills", "scripts",
    "references", "sessions", "cron", "logs",
}  # fmt: skip
DATE_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class Finding:
    level: str  # high | medium | info
    kind: str
    file: str
    detail: str
    line: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "kind": self.kind, "file": self.file, "line": self.line, "detail": self.detail}


@dataclass
class Audit:
    folder: str
    files_read: int = 0
    memory_files: list[str] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    layout: str = "unknown"

    def counts(self) -> dict[str, int]:
        out = {"high": 0, "medium": 0, "info": 0}
        for f in self.findings:
            out[f.level] += 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "folder": self.folder,
            "layout": self.layout,
            "files_read": self.files_read,
            "memory_files": self.memory_files,
            "skills": self.skills,
            "counts": self.counts(),
            "findings": [f.as_dict() for f in self.findings],
        }

    def render(self) -> str:
        lines = [
            f"Lobster Rescue audit of {self.folder}",
            f"Read {self.files_read} files. Nothing was imported or run. Layout: {self.layout}.",
        ]
        c = self.counts()
        lines.append(f"{c['high']} high, {c['medium']} medium, {c['info']} notes.")
        for f in self.findings:
            where = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"[{f.level}] {f.kind} ({where}): {f.detail}")
        if self.skills:
            lines.append(f"{len(self.skills)} skills found; none is imported or run.")
        return "\n".join(lines)


def _files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if len(out) >= MAX_FILES:
            break
        # Never follow links: a link out of the folder could pull in any file on the machine.
        if p.is_symlink() or not p.is_file() or any(part == ".git" for part in p.relative_to(root).parts):
            continue
        if p.suffix.lower() in TEXT_SUFFIXES or p.name.startswith(".env") or p.name in MEMORY_FILES:
            out.append(p)
    return out


def _read(p: Path) -> str | None:
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _is_memory(rel: Path) -> bool:
    """MEMORY.md, USER.md and DREAMS.md at the top or in memories/, and dated notes in memory/ or memories/."""
    top = rel.parts[0]
    if rel.name in MEMORY_FILES:
        return len(rel.parts) == 1 or (len(rel.parts) == 2 and top in {"memories", "memory"})
    return top in {"memory", "memories"} and rel.suffix == ".md" and len(rel.parts) == 2


def _is_skill(rel: Path) -> bool:
    return rel.name == "SKILL.md" or (rel.parts[0] == "skills" and rel.suffix == ".md")


def detect_layout(root: Path) -> str:
    """ "hermes" (a memories/ folder, or config.yaml with SOUL.md), "openclaw" (MEMORY.md or openclaw.json at the
    top) or "unknown". Names only; no file is read. The Hermes shape is from public docs and community write-ups."""
    if (root / "memories").is_dir() or ((root / "config.yaml").is_file() and (root / "SOUL.md").is_file()):
        return "hermes"
    if (root / "openclaw.json").is_file() or (root / "MEMORY.md").is_file() or (root / "memory").is_dir():
        return "openclaw"
    return "unknown"


def audit(folder: str | Path) -> Audit:
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError("That is not a folder")
    result = Audit(folder=root.name or str(root), layout=detect_layout(root))
    for path in _files(root):
        text = _read(path)
        rel = path.relative_to(root)
        if text is None:
            result.findings.append(Finding("info", "skipped", str(rel), "too large or unreadable, not read"))
            continue
        result.files_read += 1
        if _is_memory(rel):
            result.memory_files.append(str(rel))
        if path.name == "SOUL.md" and len(rel.parts) == 1:
            result.findings.append(
                Finding("info", "persona file", str(rel), "describes how the old assistant behaves; never imported")
            )
        for n, line in enumerate(text.splitlines(), 1):
            for kind, pat in SECRET_PATTERNS:
                if pat.search(line):
                    result.findings.append(
                        Finding("high", "secret in a plain file", str(rel), f"{kind} (value not shown)", n)
                    )
                    break
            if EXPOSED_BIND.search(line):
                result.findings.append(
                    Finding("high", "gateway reachable beyond this machine", str(rel), "listens on all interfaces", n)
                )
            if NO_AUTH.search(line) and "gateway" in text.lower():
                result.findings.append(
                    Finding("high", "gateway without authentication", str(rel), line.strip()[:80], n)
                )
            if ORDERS.search(line) and _is_memory(rel):
                result.findings.append(
                    Finding("medium", "memory line that gives orders", str(rel), "would be left out of an import", n)
                )
        if _is_skill(rel):
            asks = sorted({m.group(0).strip()[:40] for m in SHELL_ASK.finditer(text)})
            name = _skill_name(rel, text)
            has_scripts = (path.parent / "scripts").is_dir()
            result.skills.append(
                {
                    "name": name,
                    "file": str(rel),
                    "asks_for_shell": bool(asks),
                    "has_scripts": has_scripts,
                    "imported": False,
                }  # fmt: skip
            )
            if asks:
                result.findings.append(
                    Finding("medium", "skill asks for a shell", str(rel), f"{name}: {', '.join(asks)[:100]}")
                )
    if not result.memory_files:
        result.findings.append(
            Finding("info", "no memory files", ".", "no MEMORY.md, USER.md, DREAMS.md or memory/*.md")
        )
    return result


def _skill_name(rel: Path, text: str) -> str:
    m = re.search(r"(?m)^name:\s*(.+)$", text)
    if m:
        return clean(m.group(1))[:60]
    return rel.parent.name if rel.name == "SKILL.md" else rel.stem


def _lines(text: str) -> list[str]:
    """Bullets and short paragraphs; headings only set the title of what follows. A section sign separates
    entries (Hermes)."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n|" + ENTRY_SEPARATOR, text):
        buf: list[str] = []
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("<!--") or line.startswith("```"):
                continue
            bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", line)
            if bullet:
                if buf:
                    out.append(" ".join(buf))
                    buf = []
                out.append(bullet.group(1))
            else:
                buf.append(line)
        if buf:
            out.append(" ".join(buf))
    return [clean(x) for x in out if clean(x)]


def cards(folder: str | Path, scope: str = "notes") -> dict[str, Any]:
    """Context cards from the memory files, plus what was left out and why. Skills are never read as cards."""
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError("That is not a folder")
    out: list[dict[str, Any]] = []
    left_out = {"orders": 0, "secret": 0, "too_many": 0}
    for path in _files(root):
        rel = path.relative_to(root)
        if not _is_memory(rel):
            continue
        daily = path.name not in MEMORY_FILES
        text = _read(path)
        if text is None:
            continue
        category = MEMORY_FILES.get(path.name, "other")
        d = DATE_NAME.search(path.name)
        for line in _lines(text):
            if ORDERS.search(line):
                left_out["orders"] += 1
                continue
            if any(p.search(line) for _, p in SECRET_PATTERNS):
                left_out["secret"] += 1
                continue
            if len(out) >= MAX_CARDS_PER_REQUEST:
                left_out["too_many"] += 1
                continue
            card: dict[str, Any] = {
                "scope": scope,
                "title": path.stem[:MAX_TITLE] if daily else path.name,
                "text": line[:MAX_CARD_TEXT],
                "category": category,
            }
            if d:
                card["when"] = d.group(1)
            out.append(card)
    return {"cards": out, "left_out": left_out}


def dry_run(folder: str | Path) -> dict[str, Any]:
    """Exactly what an import would do, without doing it: per file how many cards, what is left out and why, and
    the skills that are listed only. Nothing is written, no model or network is used, `would_write` is always 0
    database rows. Card text is shortened to a preview."""
    root = Path(folder).resolve()
    report = audit(root)
    made = cards(root)
    per_file: dict[str, int] = {}
    previews: list[dict[str, str]] = []
    for c in made["cards"]:
        per_file[c["title"]] = per_file.get(c["title"], 0) + 1
        if len(previews) < 10:
            previews.append({"from": c["title"], "text": c["text"][:80]})
    return {
        "dry_run": True,
        "layout": report.layout,
        "would_import_cards": len(made["cards"]),
        "cards_per_file": dict(sorted(per_file.items())),
        "preview": previews,
        "left_out": made["left_out"],
        "skills_listed_only": [{"name": s["name"], "asks_for_shell": s["asks_for_shell"]} for s in report.skills],
        "skills_imported": 0,
        "high_findings": report.counts()["high"],
        "would_write": 0,
    }


def layout_listing(folder: str | Path, *, names: bool = False, max_entries: int = 200) -> list[str]:
    """The folder's shape for a volunteer to send back so a parser can match a real layout. By default only names
    this module already knows are shown; every other name becomes `<file>.ext` or `<dir>/`. With `names=True`
    every name is shown, for the volunteer to read first. No file is opened, and links are not followed."""
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError("That is not a folder")
    out: list[str] = []
    for p in sorted(root.rglob("*")):
        if len(out) >= max_entries:
            out.append(f"... (stopped at {max_entries} entries)")
            break
        if p.is_symlink():
            continue
        rel = p.relative_to(root)
        if ".git" in rel.parts or len(rel.parts) > 4:
            continue
        shown = []
        for i, part in enumerate(rel.parts):
            known = names or part in KNOWN_NAMES
            is_dir = root.joinpath(*rel.parts[: i + 1]).is_dir()
            shown.append(part if known else ("<dir>" if is_dir else "<file>" + Path(part).suffix))
        out.append("/".join(shown) + ("/" if p.is_dir() else ""))
    return out
