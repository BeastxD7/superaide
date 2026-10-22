"""The editable surface (issue #114, minimal form): the only things a learning loop may write, and what it may not.

Anything that learns (the Overnight Tuner, the Skill Ratchet, a sleep job) calls `guard` with what it is about
to change. A target not on the writable list, or on the protected list, is refused and audited. The grader and
this file are also pinned by a hash (`protected.sha256`): `verify_protected` fails if either changed without
the hash being re-made by a person (`python -m superaide.trust.surface --update`, reviewed in the PR).
This is the stand-in for the full manifest in #114 (database roles, CODEOWNERS); it is compatible with it.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

WRITABLE_PROMPTS = frozenset({"triage_system"})  # the prompts a learning loop may propose a change to
WRITABLE_TABLES = frozenset({"prompt_version", "playbook", "memory_item"})
# Never writable by a learning loop. Names, not paths, so a refusal reads clearly in the audit log.
PROTECTED = frozenset(
    {
        "grader", "eval_sets", "held_out_split", "invariants", "audience_gate_sql", "rls_policies",
        "stage_promotion", "tool_effect_classes", "approval_ladder", "tool_manifest", "erasure",
        "canary_suite", "write_caps", "editable_surface",
    }
)  # fmt: skip

_HERE = Path(__file__).parent
PINNED = ("grader.py", "surface.py")
HASH_FILE = _HERE / "protected.sha256"


class SurfaceError(PermissionError):
    pass


def guard(target: str) -> str:
    """The target if a learning loop may write it; else SurfaceError. `target` is `prompt:<key>`, `table:<name>`
    or one of the PROTECTED names."""
    kind, _, name = target.partition(":")
    if target in PROTECTED or name in PROTECTED:
        raise SurfaceError(f"{target} is protected: nothing that learns may change what judges it")
    if kind == "prompt" and name in WRITABLE_PROMPTS:
        return target
    if kind == "table" and name in WRITABLE_TABLES:
        return target
    raise SurfaceError(f"{target} is not on the editable surface")


def digest() -> str:
    h = hashlib.sha256()
    for name in PINNED:
        h.update(name.encode() + b"\0" + (_HERE / name).read_bytes() + b"\0")
    return h.hexdigest()


def verify_protected() -> None:
    expected = HASH_FILE.read_text().strip() if HASH_FILE.exists() else ""
    if digest() != expected:
        raise SurfaceError("the grader or the editable-surface manifest changed without a reviewed hash update")


if __name__ == "__main__":  # pragma: no cover
    if "--update" in sys.argv:
        HASH_FILE.write_text(digest() + "\n")
        print(HASH_FILE.read_text().strip())
