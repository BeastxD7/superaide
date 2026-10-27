"""ADR 0009 T11: a separate worker runs the jobs and the listeners; each bot is listened to once."""

from __future__ import annotations

import argparse
import threading
import time
import uuid

import pytest

from superaide import cli
from superaide.channels import telegram
from superaide.channels.runner import ChannelRunner, claim_listener
from superaide.platform.tenancy.context import InstanceScope

A = InstanceScope(tenant_id=uuid.uuid4(), agent_id=uuid.uuid4())
TOKEN = "111111:" + "A" * 35


@pytest.fixture
def polls(monkeypatch):
    started: list[str] = []

    def fake_poll(token, handle, stop):
        started.append(token)
        stop.wait(10)

    monkeypatch.setattr(telegram, "poll_forever", fake_poll)
    return started


def wait_until(check, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


def test_two_processes_never_poll_the_same_bot(polls):
    first, second = ChannelRunner(lambda m: None), ChannelRunner(lambda m: None)
    try:
        assert first.ensure_bots({TOKEN: A}) == ["111111"]
        assert second.ensure_bots({TOKEN: A}) == []  # the lock is held: skipped here
        assert wait_until(lambda: polls == [TOKEN])
        first.ensure_bots({})  # the bot moves away from the first process
        assert wait_until(lambda: second.ensure_bots({TOKEN: A}) == ["111111"])  # and the other takes it over
        assert wait_until(lambda: polls == [TOKEN, TOKEN])
    finally:
        first.stop()
        second.stop()


def test_a_listener_lock_is_freed_when_its_connection_closes():
    held = claim_listener("telegram:999")
    assert held is not None and claim_listener("telegram:999") is None
    held.close()  # as when a worker dies
    again = claim_listener("telegram:999")
    assert again is not None
    again.close()


def test_the_worker_runs_jobs_and_keeps_the_listeners_in_step(monkeypatch):
    calls = {"apply": 0, "start": 0, "stop": 0, "runtime_stop": 0}

    def apply_connections(self, *, listen):
        assert listen is True
        calls["apply"] += 1
        if calls["apply"] == 3:
            raise KeyboardInterrupt  # end the loop, like Ctrl-C
        return {}

    monkeypatch.setattr("superaide.runtime.Runtime.apply_connections", apply_connections)
    monkeypatch.setattr("superaide.runtime.Runtime.stop", lambda self: calls.__setitem__("runtime_stop", 1))
    monkeypatch.setattr("superaide.jobs.worker.JobWorker.start", lambda self: calls.__setitem__("start", 1))
    monkeypatch.setattr("superaide.jobs.worker.JobWorker.stop", lambda self: calls.__setitem__("stop", 1))
    monkeypatch.setattr("superaide.memory.recall.vectors.start", lambda s: None)
    args = argparse.Namespace(refresh=0.01, no_jobs=False, no_listeners=False)
    with pytest.raises(KeyboardInterrupt):
        cli._worker(args)
    assert calls == {"apply": 3, "start": 1, "stop": 1, "runtime_stop": 1}


def test_the_worker_can_run_jobs_only_and_stops_on_sigterm(monkeypatch):
    import signal

    applied: list[bool] = []
    handlers: dict[int, object] = {}
    monkeypatch.setattr("superaide.runtime.Runtime.apply_connections", lambda self, *, listen: applied.append(listen))
    monkeypatch.setattr("superaide.jobs.worker.JobWorker.start", lambda self: None)
    monkeypatch.setattr("superaide.memory.recall.vectors.start", lambda s: None)
    monkeypatch.setattr(signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    # Docker stops a container with SIGTERM: deliver it (through the handler the worker installed) shortly.
    threading.Timer(0.2, lambda: handlers[signal.SIGTERM](signal.SIGTERM, None)).start()  # type: ignore[operator]
    cli._worker(argparse.Namespace(refresh=0.05, no_jobs=False, no_listeners=True))
    assert applied == []
