"""Group frontend/src/features/dashboard into sub-folders (ADR 0015 phase 8).

Every import of these files is the absolute `@/features/dashboard/<name>`, so the move is a `git mv` plus a rewrite of
that one string shape. `data.ts` and the existing `capabilities/` and `settings/` folders stay where they are.

    python scripts/codemod_dashboard.py            # move and rewrite
    python scripts/codemod_dashboard.py --dry-run  # print what would change
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "frontend/src/features/dashboard"

GROUPS: dict[str, list[str]] = {
    "shell": ["AppSidebar", "Shell", "nav"],
    "home": ["Home", "home-lists", "QuestionCard"],
    "chat": ["ChatPage", "ChatSkeleton", "ResumeButton"],
    "approvals": ["Approvals", "ApprovalsPage", "useApprovals", "StepUpDialog", "MandatesCard", "TwinCard"],
    "reminders": ["RemindersPage", "ReminderWatcher", "useReminders"],
    "notebook": ["NotebookPage", "BrainPage", "BrainShare", "EpisodesPage", "JournalPage", "MirrorPage", "PassportPanel"],
    "people": ["PeoplePage"],
    "connections": ["ConnectionsPage", "ConnectorSettings", "GmailDrafts", "GmailPanel", "GmailReadSettings"],
    "settings": ["SettingsPage", "settings-audit", "AccessLogCard"],
    "skills": ["SkillsPage", "SkillReleasesPanel"],
    "learning": ["LearningPage", "ModelsPage"],
}
STEM_TO_GROUP = {stem: group for group, stems in GROUPS.items() for stem in stems}
IMPORT = re.compile(r"(@/features/dashboard/)([A-Za-z-]+)(?=['\"])")


def files_to_move() -> list[tuple[Path, Path]]:
    moves = []
    for src in sorted((ROOT / BASE).iterdir()):
        if not src.is_file():
            continue
        stem = src.name.split(".")[0]
        group = STEM_TO_GROUP.get(stem)
        if group:
            moves.append((src, src.parent / group / src.name))
    return moves


def rewrite(text: str) -> str:
    def fix(m: re.Match[str]) -> str:
        group = STEM_TO_GROUP.get(m.group(2))
        return f"{m.group(1)}{group}/{m.group(2)}" if group else m.group(0)

    return IMPORT.sub(fix, text)


def main() -> int:
    dry = "--dry-run" in sys.argv
    moves = files_to_move()
    unplaced = {p.name.split(".")[0] for p in (ROOT / BASE).iterdir() if p.is_file()} - set(STEM_TO_GROUP) - {"data"}
    if unplaced:
        print("not placed:", sorted(unplaced))
        return 1
    for src, dst in moves:
        print(f"{src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")
        if not dry:
            dst.parent.mkdir(exist_ok=True)
            subprocess.run(["git", "mv", str(src), str(dst)], check=True, cwd=ROOT)
    changed = 0
    for path in (ROOT / "frontend/src").rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or "node_modules" in path.parts:
            continue
        old = path.read_text()
        new = rewrite(old)
        if new != old:
            changed += 1
            if not dry:
                path.write_text(new)
    print(f"{len(moves)} files moved, {changed} files had imports rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
