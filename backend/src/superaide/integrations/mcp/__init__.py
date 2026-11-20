"""An MCP client (issue #666): the owner adds MCP servers in Settings and their tools become Ava's tools.

Everything that matters is decided here, in code: who may use a tool, whether it needs approval, how big a result may
be. The model only proposes a call. See `service.py` (the registry), `transport.py` (the SDK, one short connection per
operation), `tools.py` (what the loop offers and runs) and `routes.py` (Settings).
"""
