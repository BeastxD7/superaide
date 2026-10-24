"""Run due jobs in a background thread of the API, or as its own process."""

from __future__ import annotations

import logging
import threading
import time

from superaide.jobs.queue import run_due

log = logging.getLogger(__name__)


def schedule_recurring() -> None:
    """Make sure each hired instance's recurring work (the daily digest, the meeting scan) is
    queued, inside that instance's scope. One instance failing never stops the others."""
    from superaide.intentions.service import auto_close_delivered
    from superaide.intentions.service import reconcile as reconcile_reminders
    from superaide.platform.db.session import session_scope
    from superaide.platform.tenancy.context import default_scope, use_instance
    from superaide.platform.tenancy.service import active_scopes
    from superaide.proactive.digest import ensure_scheduled

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
            with use_instance(scope), session_scope() as s:
                from superaide.skills import gmail_inbox

                gmail_inbox.ensure_scheduled(s)  # nothing unless Gmail triage is on and connected
            with use_instance(scope), session_scope() as s:
                from superaide.memory import sleep

                sleep.ensure_scheduled(s)  # nothing unless SLEEP_JOB_ENABLED
        except Exception:
            log.exception("couldn't schedule the digest for instance %s", scope.agent_id)
        try:
            from superaide.app.accounts import owner as hired_owner
            from superaide.knowledge.erase import roll_off_third_party_messages
            from superaide.platform.config import get_settings

            days = get_settings().third_party_message_retention_days
            if days > 0:  # off unless the operator sets it
                with use_instance(scope), session_scope() as s:
                    if (who := hired_owner(s)) is not None:
                        roll_off_third_party_messages(s, who, days)
        except Exception:
            log.exception("couldn't roll off old messages of instance %s", scope.agent_id)
        try:
            with use_instance(scope), session_scope() as s:
                reconcile_reminders(s)
                auto_close_delivered(s)
        except Exception:
            log.exception("couldn't check the reminders of instance %s", scope.agent_id)
    from superaide.knowledge.pending import sweep

    try:
        sweep()  # documents shared while no AI was connected, for any brain that is ready now (issue #12)
    except Exception:
        log.exception("couldn't queue reading of the waiting documents")


class JobWorker:
    def __init__(self, interval: float = 5.0, sweep_every: float = 600.0, recurring_every: float = 300.0) -> None:
        self.interval = interval
        self.recurring_every = recurring_every  # seconds between passes that re-book recurring work
        self._last_recurring: float | None = None
        self.sweep_every = sweep_every  # seconds between sweeps for documents still waiting to be read
        self._last_sweep: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        from superaide.jobs.handlers import load

        load()
        schedule_recurring()  # includes a sweep
        self._last_sweep = self._last_recurring = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)
        self._thread.start()
        log.info("job worker started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def sweep_if_due(self, now: float | None = None) -> bool:
        """The safety net for issue #12: every `sweep_every` seconds, queue reading for any instance whose
        brain is ready while documents still wait. Returns whether it swept."""
        from superaide.knowledge.pending import sweep

        now = time.monotonic() if now is None else now
        if self._last_sweep is not None and now - self._last_sweep < self.sweep_every:
            return False
        self._last_sweep = now
        sweep()
        return True

    def recurring_if_due(self, now: float | None = None) -> bool:
        """Re-book each instance's recurring work (digest, meeting scan, reminders whose job died) every
        `recurring_every` seconds. Idempotent, so a chain whose last job gave up, or an instance hired since
        the worker started, is back within minutes (issue #86). Returns whether it ran."""
        now = time.monotonic() if now is None else now
        if self._last_recurring is not None and now - self._last_recurring < self.recurring_every:
            return False
        self._last_recurring = now
        schedule_recurring()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.recurring_if_due()
                self.sweep_if_due()
                ran = run_due(stop=self._stop)
            except Exception:  # the database may be briefly away; try again next tick
                log.exception("job worker tick failed")
                ran = 0
            if not ran:
                self._stop.wait(self.interval)
