"""Is the database at the migration the code expects? (a startup check)

Running new code against a database that has not been migrated fails halfway through a request with
"column ... does not exist" (a 500 in the middle of someone's onboarding). This notices it at start-up and says
what to do instead:

- hosted (DEPLOY_MODE=hosted): the process refuses to start, so a deploy that forgot to migrate fails loudly.
- anywhere else (a laptop, self-hosted): it logs a loud error, `/health` reports `database: behind`, and every
  API route except `/health` answers 503 with the fix ("run `uv run alembic upgrade head`") until the database
  has caught up. It re-checks every few seconds, so migrating needs no restart.

A database without an `alembic_version` table (built by `init-db`, or by the test suite) is not managed by
Alembic and is skipped. More than one migration head in the code is a repository problem, not a database one:
it is logged, never blocks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import inspect, text

from superaide.config import Settings

log = logging.getLogger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"
RECHECK_EVERY_S = 5.0
FIX = "Run `cd backend && uv run alembic upgrade head`, then reload."


class DatabaseBehind(RuntimeError):
    pass


@dataclass(frozen=True)
class DbStatus:
    state: str  # ok | behind | unknown_revision | multiple_heads | unmanaged | no_scripts
    applied: tuple[str, ...] = ()
    heads: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        applied = ", ".join(self.applied) or "none"
        heads = ", ".join(self.heads) or "none"
        if self.state == "behind":
            return f"The database is behind the code (it is at {applied}; the code expects {heads}). {FIX}"
        if self.state == "unknown_revision":
            return f"The database is at {applied}, which this code does not know (it expects {heads}). Update the code."
        if self.state == "multiple_heads":
            return f"The code has more than one migration head ({heads}): renumber or merge the newest migration."
        return ""


def code_heads() -> tuple[str, ...] | None:
    """The migration heads this code ships, or None when the migration scripts are not available."""
    if not ALEMBIC_INI.exists():
        return None
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ALEMBIC_INI))
    return tuple(sorted(ScriptDirectory.from_config(cfg).get_heads()))


def read_applied() -> tuple[str, ...] | None:
    """The revisions recorded in the database, or None when it has no alembic_version table."""
    from superaide.db.session import session_scope

    with session_scope() as s:
        if not inspect(s.connection()).has_table("alembic_version"):
            return None
        rows = s.execute(text("select version_num from alembic_version")).scalars().all()
    return tuple(sorted(str(r) for r in rows))


def status() -> DbStatus:
    heads = code_heads()
    if heads is None:
        return DbStatus("no_scripts")
    applied = read_applied()
    if applied is None:
        return DbStatus("unmanaged", (), heads)
    if len(heads) > 1:
        return DbStatus("multiple_heads", applied, heads)
    if set(applied) == set(heads):
        return DbStatus("ok", applied, heads)
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    known = {r.revision for r in script.walk_revisions()}
    if any(a not in known for a in applied):
        return DbStatus("unknown_revision", applied, heads)
    return DbStatus("behind", applied, heads)


def check(settings: Settings) -> str:
    """Run at start-up. Returns the message to show (empty when fine); raises DatabaseBehind in hosted mode."""
    try:
        st = status()
    except Exception:
        log.exception("couldn't compare the database with the code's migrations; continuing")
        return ""
    if st.state in ("behind", "unknown_revision"):
        if settings.deploy_mode == "hosted":
            raise DatabaseBehind(st.message)
        log.error("%s", "=" * 78)
        log.error("DATABASE NOT READY: %s", st.message)
        log.error("%s", "=" * 78)
        return st.message
    if st.state == "multiple_heads":
        log.error("%s", st.message)
    return ""


class Gate:
    """Holds the 'database is behind' message and re-checks it every few seconds."""

    def __init__(self, message: str = "") -> None:
        self.message = message
        self._at = time.monotonic()

    def current(self) -> str:
        if not self.message:
            return ""
        now = time.monotonic()
        if now - self._at >= RECHECK_EVERY_S:
            self._at = now
            try:
                st = status()
                if st.state not in ("behind", "unknown_revision"):
                    log.info("the database has caught up with the code")
                    self.message = ""
                else:
                    self.message = st.message
            except Exception:
                log.exception("couldn't re-check the database revision")
        return self.message
