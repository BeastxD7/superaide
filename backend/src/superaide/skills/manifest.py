"""The skill protocol in code: read a skill folder, validate it, describe it.

A skill is `SKILL.md` (Agent Skills standard) + `superaide.json` (this module's `Manifest`).
See docs/decisions/0006-skill-protocol.md. Nothing that fails `load_skill` can be installed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Effect = Literal["read_public", "read_private", "write_own", "write_external", "send", "spend"]
SIDE_EFFECTS: set[str] = {"write_external", "send", "spend"}
EFFECT_LABEL: dict[str, str] = {
    "read_public": "Read public information (the web)",
    "read_private": "Read what Ava knows about you",
    "write_own": "Save things in Ava's own lists (notes, reminders)",
    "write_external": "Change things in your connected accounts (asks you first)",
    "send": "Send messages or post on your behalf (asks you first)",
    "spend": "Spend money (asks you first)",
}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SCHEMA_PATH = Path(__file__).with_name("manifest.schema.json")


class SkillError(ValueError):
    pass


class Tool(BaseModel):
    """MCP's tool shape (name, description, inputSchema) plus the effect class Superaide enforces."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str = Field(min_length=1, max_length=1024)
    input_schema: dict[str, Any] | None = Field(default=None, alias="inputSchema")
    # A tool the model provider may run itself (ADR 0007, amending 0006); Superaide runs its own
    # stand-in for providers that can't. Replaces `server_tool` (an Anthropic type string).
    hosted: Literal["web_search", "web_fetch"] | None = None
    server_tool: str | None = Field(default=None, description="Deprecated: use hosted")
    mcp_server: str | None = None  # or a tool served by this MCP server (a key in `connectors`)
    effect: Effect

    @model_validator(mode="after")
    def one_source(self) -> Tool:
        if self.server_tool is not None and self.hosted is None:  # accepted for one release
            kind = self.server_tool.rsplit("_", 1)[0]
            if kind not in {"web_search", "web_fetch"}:
                raise ValueError(f"unknown server_tool {self.server_tool!r}; use hosted")
            self.hosted = kind  # type: ignore[assignment]
            self.server_tool = None
        if self.hosted is None and self.input_schema is None:
            raise ValueError("a tool needs an inputSchema (or is hosted)")
        return self


class Connector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    scopes: list[str] = Field(default_factory=list)
    mcp_url: str | None = None  # a remote MCP server (Streamable HTTP), if the tools come from one


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: Literal["chat", "memory", "research", "other"] = "other"
    per_use_usd: float = Field(default=0, ge=0, le=10)


class Manifest(BaseModel):
    """`superaide.json`: only what SKILL.md and MCP don't cover. Field names follow MCP's
    server.json and Claude's marketplace.json where they overlap."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_ref: str | None = Field(default=None, alias="$schema")
    id: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9]+(?:-[a-z0-9]+)*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
    publisher: str = Field(min_length=1, max_length=80)
    tier: Literal["official", "community"]
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=300)
    tools: list[Tool] = Field(default_factory=list)
    connectors: list[Connector] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    budget: Budget | None = None
    settings: dict[str, Any] | None = None  # JSON Schema for per-owner settings
    default_installed: bool = False
    removable: bool = True

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, tools: list[Tool]) -> list[Tool]:
        names = [t.name for t in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        return tools

    @model_validator(mode="after")
    def community_limits(self) -> Manifest:
        if self.tier == "community":
            if any(t.hosted for t in self.tools):
                raise ValueError("community skills can't use hosted tools")
            if any(t.effect in {"send", "spend"} for t in self.tools):
                raise ValueError("community skills can't send or spend until reviewed")
        return self

    @property
    def permissions(self) -> list[str]:
        """The distinct effect classes this skill needs, in a stable order, for the consent screen."""
        order = list(EFFECT_LABEL)
        return sorted({t.effect for t in self.tools}, key=order.index)


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: Manifest
    name: str  # SKILL.md frontmatter name
    description: str  # SKILL.md frontmatter description
    instructions: str  # SKILL.md body


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise SkillError("SKILL.md must start with YAML frontmatter between --- lines")
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"SKILL.md frontmatter isn't valid YAML: {exc}") from exc
    if not isinstance(front, dict):
        raise SkillError("SKILL.md frontmatter must be a mapping")
    return front, m.group(2).strip()


def load_skill(folder: Path) -> Skill:
    """Read and validate one skill folder. Raises SkillError with a readable reason."""
    md, manifest_path = folder / "SKILL.md", folder / "superaide.json"
    if not md.is_file():
        raise SkillError(f"{folder.name}: missing SKILL.md")
    if not manifest_path.is_file():
        raise SkillError(f"{folder.name}: missing superaide.json")
    front, body = parse_skill_md(md.read_text(encoding="utf-8"))
    name, description = str(front.get("name", "")), str(front.get("description", ""))
    # The Agent Skills standard's own rules.
    if not NAME_RE.match(name) or len(name) > 64:
        raise SkillError(f"{folder.name}: SKILL.md name must be lowercase words joined by hyphens (max 64)")
    if name != folder.name:
        raise SkillError(f"{folder.name}: SKILL.md name {name!r} must match the folder name")
    if not 1 <= len(description) <= 1024:
        raise SkillError(f"{folder.name}: SKILL.md description must be 1–1024 characters")
    try:
        manifest = Manifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise SkillError(f"{folder.name}: superaide.json isn't valid JSON: {exc}") from exc
    except ValidationError as exc:
        raise SkillError(
            f"{folder.name}: superaide.json: {exc.errors()[0]['msg']} at {exc.errors()[0]['loc']}"
        ) from exc
    if manifest.id.split("/", 1)[1] != name:
        raise SkillError(f"{folder.name}: manifest id must end in the SKILL.md name ({name})")
    return Skill(manifest=manifest, name=name, description=description, instructions=body)


def json_schema() -> dict[str, Any]:
    schema = Manifest.model_json_schema(by_alias=True)
    schema["$id"] = "https://superaide.dev/schemas/superaide.json"
    schema["title"] = "Superaide skill manifest (superaide.json)"
    return schema
