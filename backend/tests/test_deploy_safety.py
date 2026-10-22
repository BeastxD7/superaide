"""Startup check for open sign-up on self-hosted mode with server credentials (issue #82)."""

from __future__ import annotations

import logging

import pytest

from superaide.config import Settings
from superaide.security import deploy


def settings(**kw) -> Settings:
    base = {
        "deploy_mode": "self_hosted", "allow_many_owners": True, "anthropic_api_key": "sk-test-not-real",
        "telegram_bot_token": "", "slack_bot_token": "", "slack_app_token": "", "public_base_url": "",
        "frontend_origin": "http://localhost:3000", "session_cookie_secure": False,
    }  # fmt: skip
    return Settings(_env_file=None, **{**base, **kw})


def test_a_public_self_hosted_server_with_open_signup_and_server_keys_refuses_to_start():
    with pytest.raises(deploy.UnsafeDeployment):
        deploy.check(settings(public_base_url="https://ava.example.com"))
    with pytest.raises(deploy.UnsafeDeployment):
        deploy.check(settings(session_cookie_secure=True))


def test_a_development_machine_only_gets_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        deploy.check(settings())
    assert "DEPLOY_MODE is self_hosted" in caplog.text


@pytest.mark.parametrize(
    "kw",
    [
        {"deploy_mode": "hosted", "public_base_url": "https://ava.example.com"},
        {"allow_many_owners": False, "public_base_url": "https://ava.example.com"},
        {"anthropic_api_key": "", "public_base_url": "https://ava.example.com"},
        {"allow_unsafe_public_self_hosted": True, "public_base_url": "https://ava.example.com"},
    ],
)
def test_safe_or_acknowledged_setups_start(kw, caplog):
    with caplog.at_level(logging.ERROR):
        deploy.check(settings(**kw))
