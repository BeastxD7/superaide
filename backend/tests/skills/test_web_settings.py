# ruff: noqa: F811
"""#433: when Ava searches the web and how much she may use is the owner's setting, enforced and generated."""

from __future__ import annotations

from superaide.platform.db.models import AuditLog
from superaide.platform.db.session import session_scope
from superaide.skills import registry, web_settings
from tests.integrations.gmail_fake import owner_client_fixture  # noqa: F401
from tests.pipeline.test_loop import Script, turn_for


def install():
    skill = registry.catalog()[web_settings.SKILL_ID]
    with session_scope() as s:
        row = registry.install(s, web_settings.SKILL_ID, skill.manifest.permissions)
        row.settings = {}


def system_text(web=True) -> str:
    with session_scope() as s:
        turn = turn_for(s, Script("x"), web=web)
        return "\n".join(b.text for b in turn.system())


def test_defaults_are_todays_behaviour(owner_client):
    install()
    with session_scope() as s:
        assert web_settings.load(s) == {"searches_per_answer": 3, "pages_per_answer": 3, "search_when": "request"}
        assert web_settings.limits(s) == {"web_search": 3, "web_fetch": 3}
    text = system_text()
    assert "only when the owner asks about the outside world" in text
    assert "at most 3 searches and 3 pages per step" in text


def test_the_prompt_and_the_tool_limits_follow_the_settings(owner_client):
    install()
    url = "/app/web/read-settings"
    assert (
        owner_client.put(
            url, json={"searches_per_answer": 1, "pages_per_answer": 5, "search_when": "useful"}
        ).status_code
        == 200
    )
    text = system_text()
    assert "on your own when a search would clearly help" in text and "at most 1 searches and 5 pages" in text
    with session_scope() as s:
        turn = turn_for(s, Script("x"), web=True)
        tools = {t["name"]: t for t in turn.tools() if t["name"] in {"web_search", "web_fetch"}}
    assert tools["web_search"]["max_uses"] == 1 and tools["web_fetch"]["max_uses"] == 5


def test_bad_values_and_unknown_fields_are_refused_and_changes_are_audited(owner_client):
    install()
    url = "/app/web/read-settings"
    assert owner_client.put(url, json={"searches_per_answer": 50}).status_code == 422
    assert owner_client.put(url, json={"search_when": "always"}).status_code == 422
    assert owner_client.put(url, json={"bogus": 1}).status_code == 422
    assert owner_client.put(url, json={"pages_per_answer": 1}).status_code == 200
    with session_scope() as s:
        rows = [a for a in s.query(AuditLog).all() if a.action == "web_read_settings"]
    assert rows and "pages_per_answer" in str(rows[-1].detail)
    out = owner_client.get(url).json()
    assert set(out["choices"]) == set(web_settings.CHOICES) and out["settings"]["pages_per_answer"] == 1


def test_changing_settings_needs_the_capability_installed(owner_client):
    assert owner_client.get("/app/web/read-settings").status_code == 200  # the defaults
    assert owner_client.put("/app/web/read-settings", json={"pages_per_answer": 1}).status_code == 409
