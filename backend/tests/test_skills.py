"""The skill protocol: validation, install with consent, and only installed tools reach Ava."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from superaide.db.session import session_scope
from superaide.main import create_app
from superaide.skills import registry
from superaide.skills.manifest import SCHEMA_PATH, SkillError, json_schema, load_skill

PASSWORD = "correct horse battery"


def write_skill(root: Path, name: str, manifest: dict, front: str | None = None) -> Path:
    folder = root / name
    folder.mkdir()
    (folder / "SKILL.md").write_text(front or f"---\nname: {name}\ndescription: Does a thing.\n---\n\nUse it well.\n")
    (folder / "superaide.json").write_text(json.dumps(manifest))
    return folder


def manifest(**over) -> dict:
    base = {
        "id": "acme/notes-plus",
        "version": "0.1.0",
        "publisher": "Acme",
        "tier": "community",
        "title": "Notes plus",
        "summary": "Better notes.",
        "tools": [
            {
                "name": "list_notes",
                "description": "List notes",
                "inputSchema": {"type": "object", "properties": {}},
                "effect": "read_private",
                "mcp_server": "acme",
            }
        ],
        "connectors": [{"provider": "acme", "scopes": ["notes.read"], "mcp_url": "https://mcp.acme.example/mcp"}],
    }
    base.update(over)
    return base


def test_every_built_in_skill_follows_the_protocol_and_the_published_schema_is_current():
    ids = {s.manifest.id for s in registry.catalog().values()}
    assert {"superaide/memory", "superaide/reminders", "superaide/web-research"} <= ids
    assert json.loads(SCHEMA_PATH.read_text()) == json_schema()


def test_a_valid_community_skill_loads(tmp_path):
    skill = load_skill(write_skill(tmp_path, "notes-plus", manifest()))
    assert skill.manifest.permissions == ["read_private"] and skill.instructions == "Use it well."


@pytest.mark.parametrize(
    ("folder", "change", "front", "error"),
    [
        ("Notes_Plus", {}, None, "lowercase"),
        ("notes-plus", {"id": "acme/other"}, None, "must end in the SKILL.md name"),
        ("notes-plus", {}, "---\nname: wrong\ndescription: x\n---\n", "must match the folder name"),
        ("notes-plus", {}, "no frontmatter here", "frontmatter"),
        ("notes-plus", {"version": "one"}, None, "superaide.json"),
        ("notes-plus", {"surprise": True}, None, "superaide.json"),
        (
            "notes-plus",
            {"tools": [{"name": "post", "description": "Post", "inputSchema": {}, "effect": "send"}]},
            None,
            "can't send or spend",
        ),
        (
            "notes-plus",
            {
                "tools": [
                    {
                        "name": "web_search",
                        "description": "x",
                        "server_tool": "web_search_20250305",
                        "effect": "read_public",
                    }
                ]
            },
            None,
            "server tools",
        ),
    ],
)
def test_skills_that_break_the_protocol_are_refused_with_a_reason(tmp_path, folder, change, front, error):
    with pytest.raises(SkillError, match=error):
        load_skill(write_skill(tmp_path, folder, manifest(**change), front))


def test_defaults_are_installed_once_and_web_research_waits_for_consent():
    with session_scope() as s:
        active = {sk.manifest.id for sk in registry.active(s)}
        assert active == {"superaide/memory", "superaide/reminders"}
        with pytest.raises(registry.SkillUnavailable, match="don't match"):
            registry.install(s, "superaide/web-research", [])
        registry.install(s, "superaide/web-research", ["read_public"])
        assert "superaide/web-research" in {sk.manifest.id for sk in registry.active(s)}
        registry.remove(s, "superaide/reminders")
        registry.ensure_defaults(s)  # a removed default stays removed
        assert "superaide/reminders" not in {sk.manifest.id for sk in registry.active(s)}
        with pytest.raises(registry.SkillUnavailable, match="can't be removed"):
            registry.remove(s, "superaide/memory")


def test_only_installed_skills_give_ava_tools():
    with session_scope() as s:
        names = [t.get("name") for t in registry.tools_for(registry.active(s), web_allowed=True)]
        assert names == ["recall", "create_reminder", "list_reminders", "update_reminder", "cancel_reminder"]
        registry.remove(s, "superaide/reminders")
        names = [t.get("name") for t in registry.tools_for(registry.active(s), web_allowed=True)]
        assert names == ["recall"]


@pytest.fixture
def owner(agent):
    c = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    c.post("/auth/signup", json={"name": "Shashank Rao", "email": "s@example.com", "password": PASSWORD})
    c.post("/app/hire", json={"agent": "ava", "accepted_notice": True})
    return c


def test_the_owner_sees_skills_installs_with_consent_and_removes(owner, agent):
    listed = {s["id"]: s for s in owner.get("/app/skills").json()}
    web = listed["superaide/web-research"]
    assert web["installed"] is False and web["permissions"] == [
        {"id": "read_public", "label": "Read public information (the web)"}
    ]
    assert web["cost"].startswith("About US$0.01")
    assert owner.post("/app/skills/superaide/web-research/install", json={"accepted": []}).status_code == 409
    r = owner.post("/app/skills/superaide/web-research/install", json={"accepted": ["read_public"]})
    assert r.status_code == 200 and r.json()["installed"] is True
    assert owner.post("/app/skills/superaide/memory/remove").status_code == 409
    assert owner.post("/app/skills/superaide/reminders/remove").json()["installed"] is False
    assert owner.post("/app/skills/nobody/nothing/install", json={"accepted": []}).status_code == 404
    other = TestClient(create_app(agent), headers={"X-Requested-With": "superaide"})
    other.post("/auth/signup", json={"name": "Priya", "email": "p@example.com", "password": PASSWORD})
    assert other.get("/app/skills").status_code == 403
