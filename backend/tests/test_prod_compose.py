"""The production compose file must run the app in hosted mode (issue #82).

Without DEPLOY_MODE=hosted the server's own .env credentials stand in for every owner who signs up,
and owner-typed provider addresses may point at private hosts. The prod stack ships open sign-up,
so both services that build an agent must set it, whatever the operator's `.env` says.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.prod.yml"


@pytest.mark.parametrize("service", ["api", "worker"])
def test_prod_compose_sets_hosted_mode(service):
    compose = yaml.safe_load(COMPOSE.read_text())
    env = compose["services"][service]["environment"]
    assert env.get("DEPLOY_MODE") == "hosted", f"{service} must set DEPLOY_MODE=hosted in docker-compose.prod.yml"


def test_prod_env_example_documents_deploy_mode():
    text = (ROOT / ".env.prod.example").read_text()
    assert "DEPLOY_MODE" in text
