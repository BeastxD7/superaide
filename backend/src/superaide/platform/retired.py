"""Settings that used to be flags and are now always on (issue #404).

A self-hoster may still have one of these in .env or the environment. It is ignored (pydantic drops unknown names),
so startup says so once, naming the setting, instead of leaving them to think it still does something.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger(__name__)


# NAME -> what is now true. Add a line here AND a `[[retired]]` entry in backend/flags.toml (docs/flags.md is generated
# from it; tests/config/test_retired_flags.py fails when the two differ) when a flag is promoted. The registry is not
# shipped in the image, so this dict is what startup reads.
RETIRED: dict[str, str] = {
    "NOTIFY_FAILOVER": "a push that stays down is always sent on the owner's other linked channel",
    "PIPELINE_TIMING_ENABLED": "every message turn is always timed (milliseconds only, no text)",
    "TYPING_INDICATORS": "Telegram and Slack always show that Ava is working on a reply",
}


def _env_file_names(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return names
    for raw in lines:
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            names.add(line.split("=", 1)[0].removeprefix("export ").strip().upper())
    return names


def set_but_retired(environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")) -> list[str]:
    """The retired setting names present in the environment or the .env file, sorted."""
    env = os.environ if environ is None else environ
    present = {n.upper() for n in env} | (_env_file_names(env_file) if env_file else set())
    return sorted(n for n in RETIRED if n in present)


def warn(environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")) -> list[str]:
    """Log one warning per retired setting that is still set. Called once at startup."""
    found = set_but_retired(environ, env_file)
    for name in found:
        log.warning("%s is no longer a setting and is ignored: %s. Remove it from your .env.", name, RETIRED[name])
    return found
