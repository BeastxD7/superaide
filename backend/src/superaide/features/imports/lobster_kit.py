"""Lobster Rescue measurement kit (#141): the five-volunteer experiment, with nothing leaving the machine.

A volunteer runs `superaide lobster-kit FOLDER` on their own OpenClaw or Hermes workspace. The kit runs
the local leak audit, shows the volunteer the anonymous result, and writes it to a file only after they
say yes. They read the file and send it (or not) themselves; the kit never sends anything.

The result holds counts and yes/no answers only: no folder name, no file name, no line, no text, no
secret value, no secret fragment, no machine or user name. The volunteer id is random. `summarise`
combines the result files the volunteers sent back into the experiment's numbers.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from superaide.features.imports import lobster

SCHEMA = "lobster-kit/1"
# Only these fixed labels (written in lobster.py, never taken from the workspace) can appear as keys.
KINDS = {
    "secret in a plain file",
    "gateway reachable beyond this machine",
    "gateway without authentication",
    "memory line that gives orders",
    "skill asks for a shell",
    "skipped",
    "no memory files",
}


def _bucket(n: int) -> str:
    return "0" if n == 0 else "1-5" if n <= 5 else "6-20" if n <= 20 else "21+"


def anonymous_result(folder: str | Path) -> dict[str, Any]:
    report = lobster.audit(folder)
    by_kind: dict[str, int] = {}
    secret_types: dict[str, int] = {}
    for f in report.findings:
        kind = f.kind if f.kind in KINDS else "other"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if f.kind == "secret in a plain file":
            # `detail` is "<kind> (value not shown)", a label from lobster.SECRET_PATTERNS.
            label = f.detail.split(" (value", 1)[0]
            if label in {name for name, _ in lobster.SECRET_PATTERNS}:
                secret_types[label] = secret_types.get(label, 0) + 1
    shell_skills = sum(1 for s in report.skills if s["asks_for_shell"])
    return {
        "schema": SCHEMA,
        "volunteer": secrets.token_hex(4),
        "month": date.today().strftime("%Y-%m"),
        "files_read": _bucket(report.files_read),
        "memory_files": len(report.memory_files),
        "skills": len(report.skills),
        "skills_asking_for_shell": shell_skills,
        "findings_by_kind": dict(sorted(by_kind.items())),
        "secret_types": dict(sorted(secret_types.items())),
        "exposed_secret": by_kind.get("secret in a plain file", 0) > 0,
        "gateway_reachable": by_kind.get("gateway reachable beyond this machine", 0) > 0,
        "gateway_without_auth": by_kind.get("gateway without authentication", 0) > 0,
        "shell_skill": shell_skills > 0,
        "memory_orders": by_kind.get("memory line that gives orders", 0) > 0,
    }


def run(
    folder: str | Path,
    out: str | Path | None = None,
    *,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    assume_yes: bool = False,
) -> Path | None:
    """The guided script. Returns the file written, or None if the volunteer declined."""
    say("Lobster Rescue measurement kit")
    say("This reads the folder on this machine. Nothing is imported, run, or sent anywhere.")
    result = anonymous_result(folder)
    say("")
    say("Here is the whole result. It has no folder or file names, no text and no secrets:")
    say(json.dumps(result, indent=2))
    say("")
    target = Path(out) if out else Path.cwd() / f"lobster-result-{result['volunteer']}.json"
    if not assume_yes and ask(f"Save this to {target}? [y/N] ").strip().lower() not in {"y", "yes"}:
        say("Nothing was saved.")
        return None
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    say(f"Saved {target}. Open it, check it, and send it to the project owner only if you are happy with it.")
    return target


def summarise(paths: list[str | Path]) -> dict[str, Any]:
    """The experiment's numbers from the result files volunteers sent back."""
    rows = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        if data.get("schema") != SCHEMA:
            raise ValueError(f"{Path(p).name} is not a lobster-kit result")
        rows.append(data)
    n = len(rows)
    out: dict[str, Any] = {"workspaces": n}
    for key in ("exposed_secret", "gateway_reachable", "gateway_without_auth", "shell_skill", "memory_orders"):
        out[key] = sum(1 for r in rows if r.get(key))
    out["any_high_risk"] = sum(
        1 for r in rows if r.get("exposed_secret") or r.get("gateway_reachable") or r.get("gateway_without_auth")
    )
    out["skills"] = sum(int(r.get("skills", 0)) for r in rows)
    out["skills_asking_for_shell"] = sum(int(r.get("skills_asking_for_shell", 0)) for r in rows)
    return out


def render_summary(s: dict[str, Any]) -> str:
    n = s["workspaces"]
    return "\n".join(
        [
            f"{n} workspaces measured.",
            f"{s['exposed_secret']} of {n} had a secret lying in a plain file.",
            f"{s['gateway_reachable']} of {n} had a gateway reachable beyond the machine.",
            f"{s['gateway_without_auth']} of {n} had a gateway without authentication.",
            f"{s['shell_skill']} of {n} had a skill that asks for a shell "
            f"({s['skills_asking_for_shell']} of {s['skills']} skills).",
            f"{s['memory_orders']} of {n} had memory lines that give orders.",
            f"{s['any_high_risk']} of {n} had at least one high-risk exposure.",
        ]
    )
