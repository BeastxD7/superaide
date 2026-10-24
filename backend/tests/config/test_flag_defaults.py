"""#448: decided and tested features ship on by default. The suite pins a few of them off in its environment (see
tests/conftest.py); these tests read the defaults the way a fresh install does."""

import pytest

from superaide.platform.config import Settings


def fresh(monkeypatch, *names: str) -> Settings:
    for name in names:
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None)


def test_a_fresh_install_has_the_task_protocol_on(monkeypatch):
    assert fresh(monkeypatch, "TASK_PROTOCOL_ENABLED").task_protocol_enabled is True


def test_the_task_protocol_can_still_be_turned_off(monkeypatch):
    monkeypatch.setenv("TASK_PROTOCOL_ENABLED", "false")
    assert Settings(_env_file=None).task_protocol_enabled is False


@pytest.mark.parametrize("name", ["TASK_PROTOCOL_ENABLED"])
def test_the_example_env_documents_the_on_default(name):
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    assert f"\n{name}=true" in text
