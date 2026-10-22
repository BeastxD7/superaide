"""Run due jobs in a background thread of the API, or as its own process."""

from __future__ import annotations

import logging
import threading

from superaide.jobs.queue import run_due

log = logging.getLogger(__name__)


def schedule_recurring() -> None:
    """Make sure each hired instance's recurring work (the daily digest, the meeting scan) is
    queued, inside that instance's scope. One instance failing never stops the others."""
    from superaide.db.session import session_scope
    from superaide.proactive.digest import ensure_scheduled
    from superaide.tenancy.context import default_scope, use_instance
    from superaide.tenancy.service import active_scopes

    try:
        with session_scope() as s:
            scopes = active_scopes(s) or [default_scope()]
    except Exception:
        log.exception("couldn't list agent instances")
        return
    for scope in scopes:
        try:
            with use_instance(scope), session_scope() as s:
                ensure_scheduled(s)
        except Exception:
            log.exception("couldn't schedule the digest for instance %s", scope.agent_id)


class JobWorker:
    def __init__(self, interval: float = 5.0) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        from superaide.jobs.handlers import load

        load()
        schedule_recurring()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)
        self._thread.start()
        log.info("job worker started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = run_due()
            except Exception:  # the database may be briefly away; try again next tick
                log.exception("job worker tick failed")
                ran = 0
            if not ran:
                self._stop.wait(self.interval)
