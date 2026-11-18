"""Settings that were renamed (issue #405): the old name still works for one release and says so at startup.

The experiments (drills, desks and the like) moved to an EXPERIMENT_ prefix. `config.Settings` accepts both names
(`AliasChoices(new, old)`; the new one wins when both are set). This module only reports that an old name is in use.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger(__name__)

# old name -> new name. Remove an entry (and the old alias in config.py) one release after it was added.
RENAMED: dict[str, str] = {
    f"{n}_ENABLED": f"EXPERIMENT_{n}"
    for n in (
        "AUDITION",
        "MODEL_DRILL",
        "REPLAY_BEFORE_UPDATE",
        "DISSENT_DESK",
        "FORK_NIGHT",
        "OWNER_TWIN",
        "TIME_TRAVEL_RULES",
        "MIRROR",
        "REHEARSAL",
        "SECOND_OPINION",
        "WHY_REPLAY",
        "OVERNIGHT_TUNER",
    )
}


def _env_file_names(path: Path) -> set[str]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return set()
    names: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            names.add(line.split("=", 1)[0].removeprefix("export ").strip().upper())
    return names


def old_names_in_use(environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")) -> list[str]:
    env = os.environ if environ is None else environ
    present = {n.upper() for n in env} | (_env_file_names(env_file) if env_file else set())
    return sorted(n for n in RENAMED if n in present)


def warn(environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")) -> list[str]:
    """One warning per old name still set. Called once at startup."""
    found = old_names_in_use(environ, env_file)
    for name in found:
        log.warning(
            "%s is renamed %s; the old name works for one more release. Rename it in your .env.", name, RENAMED[name]
        )
    return found
