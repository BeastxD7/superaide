"""The editable-surface manifest and its enforcement (issue #114). See surface.toml for the lists.

python -m superaide.security.surface --update   rewrite surface.lock.json after a reviewed change
python -m superaide.security.surface            check; exit 1 on any difference
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

HERE = Path(__file__).parent
MANIFEST = HERE / "surface.toml"
LOCK = HERE / "surface.lock.json"
REPO = HERE.parents[3]  # backend/src/superaide/security -> the repo root
LOCK_REL = LOCK.relative_to(REPO).as_posix()
MANIFEST_REL = MANIFEST.relative_to(REPO).as_posix()


@dataclass(frozen=True)
class Surface:
    writable_paths: tuple[str, ...]
    writable_tables: frozenset[str]
    protected_paths: tuple[str, ...]
    protected_tables: frozenset[str]


def load() -> Surface:
    data = tomllib.loads(MANIFEST.read_text())
    w, p = data["writable"], data["protected"]
    return Surface(
        writable_paths=tuple([*w["prompts"], *w["skills"]]),
        writable_tables=frozenset(w["tables"]),
        protected_paths=tuple(p["paths"]),
        protected_tables=frozenset(p["tables"]),
    )


def _match(path: str, pattern: str) -> bool:
    """`**` crosses folders, `*` does not."""
    parts = PurePosixPath(pattern).parts
    target = PurePosixPath(path).parts
    if "**" in parts:
        i = parts.index("**")
        return target[:i] == parts[:i] and len(target) > i  # the folder, any depth below it
    return len(parts) == len(target) and all(fnmatch.fnmatchcase(t, q) for t, q in zip(target, parts, strict=True))


def _norm(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/"))).lstrip("/")


def check_edit(paths: list[str], tables: list[str] | None = None, *, surface: Surface | None = None) -> list[str]:
    """Why a candidate self-edit is refused (empty = allowed). A path must be inside the writable surface AND
    outside the protected one; a `..` or an absolute path is refused outright. Unknown means refused."""
    sf = surface or load()
    problems: list[str] = []
    for raw in paths:
        p = _norm(raw)
        if ".." in PurePosixPath(p).parts or raw.startswith(("/", "~")):
            problems.append(f"{raw}: path escapes the repository")
        elif any(_match(p, g) for g in sf.protected_paths):
            problems.append(f"{raw}: protected")
        elif not any(_match(p, g) for g in sf.writable_paths):
            problems.append(f"{raw}: not on the editable surface")
    for t in tables or []:
        if t in sf.protected_tables:
            problems.append(f"table {t}: protected")
        elif t not in sf.writable_tables:
            problems.append(f"table {t}: not on the editable surface")
    return problems


def protected_files(sf: Surface | None = None, root: Path = REPO) -> list[str]:
    sf = sf or load()
    found: set[str] = set()
    for f in root.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts and ".venv" not in f.parts:
            rel = f.relative_to(root).as_posix()
            if rel != LOCK_REL and any(_match(rel, g) for g in sf.protected_paths):
                found.add(rel)
    return sorted(found)


_TABLE_LIST = re.compile(r"^TABLES: tuple\[str, \.\.\.\] = \(.*?^\)\n", re.DOTALL | re.MULTILINE)


def digest(path: Path) -> str:
    """The file's hash. For db/rls.py the list of table names is left out: every new table adds a line to it,
    and that is routine; the policy text, roles and the exempt list are what is locked."""
    data = path.read_bytes()
    if path.name == "rls.py" and path.parent.name == "db":
        data = _TABLE_LIST.sub("TABLES = (...)\n", data.decode()).encode()
    return hashlib.sha256(data).hexdigest()


def current(root: Path = REPO) -> dict[str, str]:
    # The manifest itself is protected: the allowlist can't be edited by what it limits.
    files = {rel: digest(root / rel) for rel in protected_files(root=root)}
    files[MANIFEST_REL] = digest(root / MANIFEST_REL)
    return dict(sorted(files.items()))


def differences(root: Path = REPO, *, require_all: bool = True) -> list[str]:
    """What differs from the lock. With `require_all` False (an installed package that has no tests or eval
    folders) files that are absent are skipped, but files that are present must match."""
    locked = json.loads(LOCK.read_text())
    now = current(root)
    out = [f"changed: {p}" for p, h in locked.items() if p in now and now[p] != h]
    out += [f"new protected file: {p}" for p in now if p not in locked]
    if require_all:
        out += [f"missing: {p}" for p in locked if p not in now]
    return out


def verify_boot() -> None:
    """Refuse to start when a protected file differs from the lock (SURFACE_CHECK_ON_BOOT)."""
    if not (REPO / "backend").is_dir():
        return  # an installed package: there is no repo to compare
    problems = differences(require_all=False)
    if problems:
        raise RuntimeError("A protected file changed without its lock being updated: " + "; ".join(problems[:5]))


if __name__ == "__main__":
    if "--update" in sys.argv:
        LOCK.write_text(json.dumps(current(), indent=2) + "\n")
        print(f"Locked {len(current())} protected files.")
    else:
        bad = differences()
        print("\n".join(bad) or "Protected files match the lock.")
        sys.exit(1 if bad else 0)
