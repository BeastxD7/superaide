"""The in-process listener runner: it follows the connected tokens and never takes the API down.

The real listeners (Telegram long-polling, Slack Socket Mode) are replaced by fakes, so
these tests open no connections and finish in milliseconds.
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from superaide.channels import slack, telegram
from superaide.channels.runner import ChannelRunner

TG_A = "111:" + "A" * 35
TG_B = "222:" + "B" * 35


class FakePolling:
    """Stands in for telegram.poll_forever: blocks until its stop event is set."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, threading.Event]] = []
        self.started = threading.Semaphore(0)
        self.crash = False

    def __call__(self, token: str, handle: Any, stop: threading.Event) -> None:
        self.calls.append((token, handle, stop))
        self.started.release()
        if self.crash:
            raise RuntimeError("listener crashed")
        stop.wait(5)

    def wait_started(self, n: int = 1) -> None:
        for _ in range(n):
            assert self.started.acquire(timeout=1), "listener thread never started"


class FakeSocket:
    def __init__(self, bot: str, app: str, team: str, handle: Any) -> None:
        self.args = (bot, app, team)
        self.handle = handle
        self.connected = self.closed = False
        self.close_raises = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True
        if self.close_raises:
            raise RuntimeError("socket already gone")


@pytest.fixture
def polling(monkeypatch) -> FakePolling:
    fake = FakePolling()
    monkeypatch.setattr(telegram, "poll_forever", fake)
    return fake


@pytest.fixture
def sockets(monkeypatch) -> list[FakeSocket]:
    built: list[FakeSocket] = []

    def factory(*args: Any) -> FakeSocket:
        s = FakeSocket(*args)
        built.append(s)
        return s

    monkeypatch.setattr(slack, "socket_mode_handler", factory)
    return built


@pytest.fixture
def runner():
    r = ChannelRunner(lambda msg: None)
    yield r
    r.stop()


def connected(settings, **tokens: str):
    return settings.model_copy(update=tokens)


def threads_named(name: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == name]


def test_nothing_starts_when_no_channel_is_connected(runner, settings, polling, sockets):
    assert runner.ensure(settings) == {"telegram": False, "slack": False}
    assert polling.calls == [] and sockets == []


def test_connecting_telegram_starts_one_daemon_polling_thread(runner, settings, polling, sockets):
    assert runner.ensure(connected(settings, telegram_bot_token=TG_A)) == {"telegram": True, "slack": False}
    polling.wait_started()
    [(token, handle, stop)] = polling.calls
    assert token == TG_A and handle is runner.handle and not stop.is_set()
    [thread] = threads_named("telegram-listener")
    assert thread.daemon and sockets == []


def test_applying_the_same_telegram_token_again_keeps_the_running_listener(runner, settings, polling):
    s = connected(settings, telegram_bot_token=TG_A)
    runner.ensure(s)
    runner.ensure(s)
    runner.ensure(s)
    polling.wait_started()
    assert len(polling.calls) == 1


def test_changing_the_telegram_token_restarts_the_listener_with_the_new_one(runner, settings, polling):
    runner.ensure(connected(settings, telegram_bot_token=TG_A))
    polling.wait_started()
    old_thread = runner._telegram[2]
    runner.ensure(connected(settings, telegram_bot_token=TG_B))
    polling.wait_started()
    (_, _, old_stop), (new_token, _, new_stop) = polling.calls
    assert old_stop.is_set() and not new_stop.is_set() and new_token == TG_B
    old_thread.join(timeout=1)
    assert not old_thread.is_alive()


def test_disconnecting_telegram_stops_its_listener(runner, settings, polling):
    runner.ensure(connected(settings, telegram_bot_token=TG_A))
    polling.wait_started()
    thread = runner._telegram[2]
    assert runner.ensure(settings) == {"telegram": False, "slack": False}
    thread.join(timeout=1)
    assert polling.calls[0][2].is_set() and not thread.is_alive()


def test_slack_needs_both_the_bot_and_the_app_token(runner, settings, polling, sockets):
    assert runner.ensure(connected(settings, slack_bot_token="xoxb-1")) == {"telegram": False, "slack": False}
    assert runner.ensure(connected(settings, slack_app_token="xapp-1")) == {"telegram": False, "slack": False}
    assert sockets == []
    s = connected(settings, slack_bot_token="xoxb-1", slack_app_token="xapp-1", slack_team_id="T_OURS")
    assert runner.ensure(s) == {"telegram": False, "slack": True}
    [sock] = sockets
    assert sock.args == ("xoxb-1", "xapp-1", "T_OURS") and sock.connected and sock.handle is runner.handle


def test_applying_the_same_slack_tokens_again_keeps_the_socket(runner, settings, sockets):
    s = connected(settings, slack_bot_token="xoxb-1", slack_app_token="xapp-1")
    runner.ensure(s)
    runner.ensure(s)
    assert len(sockets) == 1 and not sockets[0].closed


def test_changing_a_slack_token_closes_the_old_socket_before_opening_a_new_one(runner, settings, sockets):
    runner.ensure(connected(settings, slack_bot_token="xoxb-1", slack_app_token="xapp-1"))
    runner.ensure(connected(settings, slack_bot_token="xoxb-1", slack_app_token="xapp-2"))
    old, new = sockets
    assert old.closed and new.connected and not new.closed and new.args[1] == "xapp-2"


def test_a_socket_that_fails_to_close_is_still_replaced(runner, settings, sockets, caplog):
    runner.ensure(connected(settings, slack_bot_token="xoxb-1", slack_app_token="xapp-1"))
    sockets[0].close_raises = True
    with caplog.at_level(logging.ERROR, logger="superaide.channels.runner"):
        result = runner.ensure(connected(settings, slack_bot_token="xoxb-2", slack_app_token="xapp-1"))
    assert result["slack"] is True and len(sockets) == 2 and sockets[1].connected
    assert "closing Slack listener" in caplog.text


def test_a_slack_listener_that_cannot_start_is_reported_and_retried_later(
    runner, settings, polling, monkeypatch, caplog
):
    attempts: list[str] = []

    def broken(bot, app, team, handle):
        attempts.append(bot)
        raise ConnectionError("invalid_auth")

    monkeypatch.setattr(slack, "socket_mode_handler", broken)
    s = connected(settings, telegram_bot_token=TG_A, slack_bot_token="xoxb-bad", slack_app_token="xapp-1")
    with caplog.at_level(logging.ERROR, logger="superaide.channels.runner"):
        assert runner.ensure(s) == {"telegram": True, "slack": False}
        assert runner.ensure(s) == {"telegram": True, "slack": False}
    assert attempts == ["xoxb-bad", "xoxb-bad"]  # not remembered as running, so the next apply retries
    assert "Slack listener failed to start" in caplog.text
    polling.wait_started()
    assert len(polling.calls) == 1


def test_a_slack_socket_whose_connect_fails_is_not_kept(runner, settings, monkeypatch):
    class NoConnect(FakeSocket):
        def connect(self) -> None:
            raise OSError("network unreachable")

    monkeypatch.setattr(slack, "socket_mode_handler", NoConnect)
    s = connected(settings, slack_bot_token="xoxb-1", slack_app_token="xapp-1")
    assert runner.ensure(s)["slack"] is False and runner._slack is None


def test_stop_shuts_down_every_listener(settings, polling, sockets):
    runner = ChannelRunner(lambda msg: None)
    runner.ensure(connected(settings, telegram_bot_token=TG_A, slack_bot_token="xoxb-1", slack_app_token="xapp-1"))
    polling.wait_started()
    thread = runner._telegram[2]
    runner.stop()
    thread.join(timeout=1)
    assert not thread.is_alive() and sockets[0].closed
    assert runner._telegram is None and runner._slack is None
    runner.stop()  # stopping twice is harmless


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_crashing_telegram_listener_leaves_the_runner_and_slack_working(runner, settings, polling, sockets):
    polling.crash = True
    s = connected(settings, telegram_bot_token=TG_A, slack_bot_token="xoxb-1", slack_app_token="xapp-1")
    runner.ensure(s)
    polling.wait_started()
    runner._telegram[2].join(timeout=1)
    assert sockets[0].connected and not sockets[0].closed
    runner.ensure(connected(settings, slack_bot_token="xoxb-1", slack_app_token="xapp-1"))  # still usable


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_applying_connections_again_revives_a_telegram_listener_that_died(runner, settings, polling):
    polling.crash = True
    s = connected(settings, telegram_bot_token=TG_A)
    runner.ensure(s)
    polling.wait_started()
    runner._telegram[2].join(timeout=1)
    polling.crash = False
    runner.ensure(s)
    assert len(polling.calls) == 2


# --- inside the API ---------------------------------------------------------------


@pytest.fixture
def live_app(monkeypatch):
    """create_app() without a fixed agent, as in production, but with fake listeners."""
    main = importlib.import_module("superaide.main")
    calls: dict[str, list[Any]] = {"ensure": [], "stop": []}

    class SpyRunner(ChannelRunner):
        crash = False

        def ensure(self, settings):
            calls["ensure"].append(settings)
            if SpyRunner.crash:
                raise RuntimeError("listener exploded on start")
            return {"telegram": False, "slack": False}

        def stop(self):
            calls["stop"].append(True)

    monkeypatch.setattr(main, "ChannelRunner", SpyRunner)
    return main.create_app, SpyRunner, calls


def test_the_api_starts_its_listeners_on_startup_and_stops_them_on_shutdown(live_app):
    create_app, _, calls = live_app
    with TestClient(create_app()) as c:
        assert c.get("/health").json() == {"status": "ok"}
        assert len(calls["ensure"]) == 1 and calls["stop"] == []
    assert calls["stop"] == [True]


def test_a_listener_that_crashes_on_startup_does_not_take_down_the_api(live_app, caplog):
    create_app, spy, calls = live_app
    spy.crash = True
    with caplog.at_level(logging.ERROR, logger="superaide.main"), TestClient(create_app()) as c:
        assert c.get("/health").status_code == 200
    assert len(calls["ensure"]) == 1 and "couldn't start channel listeners" in caplog.text


def test_the_api_leaves_listeners_alone_when_channels_run_elsewhere(live_app, monkeypatch):
    from superaide import config

    monkeypatch.setenv("RUN_CHANNELS_IN_API", "false")
    config.get_settings.cache_clear()
    try:
        create_app, _, calls = live_app
        with TestClient(create_app()) as c:
            assert c.get("/health").status_code == 200
        assert calls["ensure"] == []
    finally:
        monkeypatch.delenv("RUN_CHANNELS_IN_API")
        config.get_settings.cache_clear()
