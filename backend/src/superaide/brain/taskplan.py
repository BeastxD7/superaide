# ruff: noqa: E501  (prompt text and owner-facing messages)
"""The human-like task protocol (issue #330), behind TASK_PROTOCOL_ENABLED (on by default).

A good human assistant handed a multi-step errand does not go straight to "may I send this?". She states the
goal, reads what is already there (the thread, the calendar, her notes), picks a route she can actually
execute, checks her own ability, and only then asks for a decision, with a recommendation and no hedging.

The split is the rest of the app's: the model PROPOSES a plan through the `record_plan` tool and code DECIDES
whether it is good enough. `check_plan` is that decision. `propose_action` is refused (no approval is created)
until a plan that passes is on file for the turn, and the plan travels with the approval so the owner sees the
pre-flight facts beside the card. With the flag off none of this is in the prompt, the tool list or the gate.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from superaide.policy import recipients

# Tools whose success means she looked before proposing (mail, calendar, her own notes, the web).
CONTEXT_TOOLS = frozenset(
    {"gmail_search", "gmail_read_thread", "calendar_events", "calendar_free_busy", "recall", "web_search", "web_fetch"}
)

PROTOCOL_RULES = """Doing a multi-step errand (anything that ends in a message, booking, cancellation or other action for the owner), work like a thoughtful human assistant, in this order:
1. Goal: what the owner really wants, in one line. The message is only the means.
2. Context first: read before you ask. Search the mail thread, calendar or your notes (gmail_search, gmail_read_thread, calendar_events, recall) and notice conflicts with what the owner said earlier.
3. Route: who will actually read what you send, and how? An automated or unmonitored address is not a route.
4. Ability: can you execute that route yourself with the tools you have? If not, say so plainly now and hand over ready-to-paste text instead.
5. Call record_plan with the goal, what you found, the route, whether you can do it (ability_ok) and your recommendation. propose_action is refused until a plan that passes is on file.
6. Then one clear message: what you found, what you recommend, what you need. No hedging next to a request for approval, and nothing is done until the owner decides."""

PLAN_TOOL = {
    "name": "record_plan",
    "description": (
        "Record your plan for a multi-step errand BEFORE you ask the owner to approve anything. Read first "
        "(mail, calendar, notes). The app checks it: without something read this turn, a route, an honest "
        "ability check and a plain recommendation, propose_action is refused."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "What the owner really wants, in one line"},
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What you found by reading, one short item each (e.g. 'last Bluehost mail: renews 3 Nov')",
            },
            "route": {"type": "string", "description": "How it will reach a person who can act, and who that is"},
            "ability_ok": {"type": "boolean", "description": "True only if YOU can execute that route with your tools"},
            "recommendation": {"type": "string", "description": "The option you recommend, stated plainly, no hedging"},
        },
        "required": ["goal", "evidence", "route", "ability_ok", "recommendation"],
        "additionalProperties": False,
    },
}

NO_PLAN = (
    "Refused, NO approval was created: this is a multi-step errand and no plan is on file for this turn. Read the "
    "relevant history first (gmail_search, gmail_read_thread, calendar_events or recall), then call record_plan "
    "(goal, evidence, route, ability_ok, recommendation), then ask."
)
CANNOT = (
    "Refused, NO approval was created: your plan says you cannot execute that route yourself (ability_ok is "
    "false). Tell the owner so plainly, and hand over what you can: a ready-to-paste, self-contained message "
    "(who you are writing for, the history, the account details, the ask, the deadline) and the exact page or "
    "address to use."
)


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=8, max_length=300)
    evidence: list[str] = Field(min_length=1, max_length=8)
    route: str = Field(min_length=3, max_length=300)
    ability_ok: StrictBool
    recommendation: str = Field(min_length=3, max_length=500)


def check_plan(args: dict[str, Any], ok_tools: set[str] | frozenset[str]) -> TaskPlan | str:
    """The plan if it passes the gates, else why not (written for the model to act on)."""
    if not (set(ok_tools) & CONTEXT_TOOLS):
        return "Plan refused: nothing was read this turn. Search the mail, calendar or your notes first, then record the plan."
    try:
        plan = TaskPlan.model_validate(args)
    except ValidationError as e:
        fields = sorted({str(err["loc"][0]) for err in e.errors() if err["loc"]})
        return (
            f"Plan refused: missing or malformed {', '.join(fields) or 'fields'}. Fill every field with real content."
        )
    plan = plan.model_copy(update={"evidence": [x.strip()[:200] for x in plan.evidence if x.strip()]})
    if not plan.evidence:
        return "Plan refused: the evidence is empty. Say what you found by reading."
    if recipients.has_hedge(plan.recommendation) or recipients.has_hedge(plan.route):
        return "Plan refused: the route or recommendation hedges. Resolve the doubt (read more, or change the route) before planning."
    return plan


def plan_line(plan: TaskPlan) -> str:
    """The owner-facing line, written by code from the validated plan (not by the model)."""
    return f"Plan: {plan.goal.rstrip('.')}. Checked: {len(plan.evidence)} source(s). Route: {plan.route.rstrip('.')}. Recommended: {plan.recommendation.rstrip('.')}."
