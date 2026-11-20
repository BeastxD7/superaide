"""The public entry point of `integrations/mcp/`: what the app wiring and the owner's loop use."""

from __future__ import annotations

from superaide.integrations.mcp.routes import make_router
from superaide.integrations.mcp.tools import offered, owns, run

__all__ = ["make_router", "offered", "owns", "run"]
