"""What policy knows about action kinds, without importing the modules that carry them out.

ADR 0015 (technique 2). The rule tables and the registries used to sit in `policy/actions.py`, which imported
`skills.calendar`, `skills.gmail`, `asklink.service` and `voice.gateway` to find out what an approved action
does. Now the modules that own a kind register it here when they are imported (`superaide.plugins` imports them
at startup), and `policy` imports none of them. `policy.actions` re-exports every name below.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from superaide.approvals.models import Approval
from superaide.identity.trust import Trust


@dataclass(frozen=True)
class Rule:
    min_trust: int
    manager_only: bool
    needs_approval: bool


RULES: dict[str, Rule] = {
    "publish_post": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "message_someone_else": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "grant_access": Rule(int(Trust.STEPPED_UP), manager_only=True, needs_approval=True),
    "delete_memory": Rule(int(Trust.STEPPED_UP), manager_only=True, needs_approval=True),
}
# Footprint Guard (#136): things created in the world under the owner's name. Only offered and accepted when
# FOOTPRINT_GUARD_ENABLED; always manager-only and always approved by the owner first.
CREATION_RULES: dict[str, Rule] = {
    kind: Rule(int(Trust.LINKED), manager_only=True, needs_approval=True)
    for kind in ("create_account", "create_profile", "publish_post", "open_pull_request", "post_review")
}
# Actions a skill's own tool asks for (never through propose_action, whose kinds are RULES): the
# tool fills in exactly what would be done, and approving runs it (EXECUTORS).
TOOL_RULES: dict[str, Rule] = {
    "calendar_create_event": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "calendar_move_event": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "calendar_decline_event": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    # #668: a reply or a new email from the owner's Outlook. Always the owner's yes (no mandate, no batching): the
    # payload is the exact text and recipients.
    "m365_mail_send": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    # #669: a new issue or a comment in a repository the owner chose. Always the owner's yes (no mandate, no
    # batching): the payload is the exact text.
    "github_create_issue": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "github_comment": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    # #669: a new page or a comment under a page the owner chose. Always the owner's yes (no mandate, no batching):
    # the payload is the exact text.
    # #669: a new issue or a comment in a Linear team the owner chose. Always the owner's yes.
    "linear_create_issue": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "linear_comment": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "notion_create_page": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "notion_comment": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    # #667: a new Doc or Sheet, or text added to a Doc Ava made, in the owner's Drive. Always the owner's yes
    # (no mandate, no batching): the payload is the exact text.
    "drive_create_file": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    "drive_append_doc": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    # #478: a message the owner asked Ava to send to someone who wrote to her (reception/relay.py).
    "relay_message": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
    # #666: a call to a tool of one of the owner's MCP servers (integrations/mcp/tools.py).
    "mcp_tool_call": Rule(int(Trust.LINKED), manager_only=True, needs_approval=True),
}
# Never through a chat channel, whatever the sender's trust: these need the signed-in app (or the
# operator's CLI). Anything not in RULES is refused too; this set names them so the refusal is explicit
# and tested. Approving is never an action at all: approvals are decided only by the app's routes.
NEVER_VIA_CHAT = {
    "change_identity",
    "change_trust",
    "approve_action",
    "change_connection",
    "change_credentials",
    "change_settings",
    "export_data",
    "delete_data",
}

# What each approved action actually does, registered by the module that owns the kind.
EXECUTORS: dict[str, Callable[[Session, Approval], str]] = {}
# What to clean up when the owner says no to a kind (gmail_send deletes the saved draft).
DISCARDERS: dict[str, Callable[[Session, Approval], None]] = {}
