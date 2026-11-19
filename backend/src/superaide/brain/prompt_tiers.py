# ruff: noqa: E501  (prompt text and the table in the docstring)
"""Three prompt sizes, chosen in code from the model's window and the kind of turn (#641).

| Tier    | When                                                    | Fixed prompt  | Tools                           | History      |
| ------- | ------------------------------------------------------- | ------------- | ------------------------------- | ------------ |
| full    | window of 100k tokens or more (Claude, big hosted ones) | as always     | all                             | as always    |
| compact | 8k to 100k tokens                                       | 2,500 or less | 3 to 6 chosen by code per turn  | 1,500 tokens |
| minimal | under 8k tokens, or small talk on a window under 100k   | 600 or less   | none                            | last 2 turns |

What a tier changes is size, never authority. Who the sender is, what they may see (the audience filter runs in the
database query before any text exists), what is tainted and what needs approval are decided by the same code in every
tier: this module only receives text and tool definitions that were already built for the person in the chat, and
only ever removes or shortens them. It never adds a tool: the tools of a shared channel stay the shared set. A tool
that was not offered and is called anyway is refused by the loop. Nothing here asks the model anything.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from superaide.brain import context_window as cw
from superaide.brain import prompt_budget as pb
from superaide.brain.llm import SystemBlock

FULL, COMPACT, MINIMAL = "full", "compact", "minimal"
FULL_WINDOW = 100_000
COMPACT_WINDOW = 8_000
COMPACT_HISTORY_TOKENS = 1_500
MINIMAL_HISTORY_MESSAGES = 4  # the last two turns
MAX_COMPACT_TOOLS = 6
SHORT_DESCRIPTION = 160  # characters kept of a tool's description in the compact tier
SHORT_PROPERTY = 60

SMALL_TALK = frozenset(
    "hi hello hey yo hiya ok okay k kk thanks thank you thx ty cool great nice good morning afternoon evening night "
    "bye goodbye cheers yes yep yeah no nope sure alright got it perfect awesome lol haha".split()
)

CORE_TOOLS = ("recall", "create_reminder", "list_approvals")
ALWAYS_IF_OFFERED = ("answer_in_dm",)  # a channel's way to move a private answer to the owner's DM
# Words in the owner's message that switch a tool (or a family, by prefix) on. Lower case, matched as substrings.
WORDS: dict[str, tuple[str, ...]] = {
    "inbox": ("inbox", "who wrote", "who messaged", "anyone", "waiting", "asked about me", "any messages"),
    "relay_message": ("tell ", "let ", "relay", "pass on", "give them", "message them", "reply to"),
    "channel_activity": ("channel", "slack", "telegram", "room", "group", "thread"),
    "set_presence": ("busy", "driving", "focus", "away", "back at", "back in", "offline", "do not disturb", "in a meeting"),
    "gmail_": ("email", "e-mail", "mail", "gmail", "draft", "inbox"),
    "calendar_": ("calendar", "meeting", "schedule", "event", "free ", "available", "appointment", "agenda"),
    "create_event": ("book", "schedule", "set up a meeting", "add an event"),
    "move_event": ("move", "reschedule", "postpone"),
    "decline_event": ("decline", "cancel the meeting"),
    "list_reminders": ("reminder", "remind", "what's due", "due today"),
    "update_reminder": ("reminder", "remind", "move", "change", "reschedule"),
    "cancel_reminder": ("cancel", "remove", "delete", "reminder"),
    "web_": ("search", "look up", "lookup", "google", "news", "http", "website", "latest", "price of", "weather"),
    "propose_action": (
        "send", "post", "approve", "delete", "grant", "share", "publish", "reply", "email", "book", "pay", "buy", "forward"
    ),
}  # fmt: skip

# Which rules of OWNER_RULES belong to which tools: a line is kept when one of its tags is offered. A line with no
# tag is general and stays. The first block is kept whatever is offered (the limits on what Ava may do herself).
_ALWAYS = ("You cannot post, grant access", "Be brief", "metric units")
_RULE_TAGS: tuple[tuple[str, str], ...] = (
    ("recall tool", "recall"),
    ("sources conflict", "recall"),
    ("create_reminder", "create_reminder"),
    ("list_approvals", "list_approvals"),
    ("web research", "web_"),
    ("relay_message", "relay_message"),
    ("set_presence", "set_presence"),
    ("gmail_", "gmail_"),
    ("What you can do right now", "-"),  # the capability block is not in the compact prompt
    ("Channel linking", "-"),
    ("Notification the app sent", "-"),
)
_HISTORY_LINES = (
    "Earlier messages happened",
    "Earlier turns",
    "Your earlier replies",
    "Don't volunteer corrections",
    "one assistant on every",
)

MINIMAL_RULES = (
    "Be brief. No tools in this reply: if the owner wants something done or looked up, say you'll do it once "
    "they ask outright; never claim you did. You cannot post, grant access or delete."
)


@dataclass
class Shaped:
    tier: str
    system: list[SystemBlock]
    tools: list[dict[str, Any]]
    msgs: list[dict[str, Any]]
    turn_start: int


def is_small_talk(text: str) -> bool:
    """A greeting or an acknowledgement: nothing in it asks for anything. Decided on the words alone."""
    words = re.findall(r"[a-z']+", text.lower())
    return 0 < len(words) <= 4 and all(w in SMALL_TALK for w in words)


def select_tier(window: cw.WindowInfo, text: str, *, pending: bool) -> str:
    """The prompt size for this turn. `pending`: an approval or a person is waiting, so nothing is small talk."""
    if window.source == "default" and window.tokens == cw.DEFAULT_HOSTED:
        return FULL  # a hosted model nobody described: assumed modest by the budget, but not stripped of its tools
    if window.tokens >= FULL_WINDOW:
        return FULL
    if window.tokens < COMPACT_WINDOW or (is_small_talk(text) and not pending):
        return MINIMAL
    return COMPACT


def choose_tools(available: list[dict[str, Any]], text: str, *, waiting: bool, pending: bool) -> list[dict[str, Any]]:
    """3 to 6 of the tools this turn already has: the core, those the state calls for, and those the words call for.
    Order of preference decides which stay when more than six match. Never returns a tool not in `available`."""
    by_name = {str(t.get("name")): t for t in available}
    picked: list[str] = [n for n in (*CORE_TOOLS, *ALWAYS_IF_OFFERED) if n in by_name]
    if waiting:
        picked += [n for n in ("inbox", "relay_message") if n in by_name]
    lowered = text.lower()
    for key, words in WORDS.items():
        if any(w in lowered for w in words):
            picked += [n for n in by_name if n.startswith(key) and n not in picked]
    if pending and "propose_action" in by_name:
        picked.append("propose_action")
    picked = list(dict.fromkeys(picked))
    core = [n for n in picked if n in (*CORE_TOOLS, *ALWAYS_IF_OFFERED)]
    extra = [n for n in picked if n not in core]
    return [shorten(by_name[n]) for n in [*core, *extra][: max(MAX_COMPACT_TOOLS, len(core))]]


def shorten(tool: dict[str, Any]) -> dict[str, Any]:
    """The same tool with a shorter description and shorter property notes: the policy text is enforced in code anyway."""
    out = copy.deepcopy(tool)
    if isinstance(out.get("description"), str):
        out["description"] = _first(out["description"], SHORT_DESCRIPTION)
    props = (out.get("input_schema") or {}).get("properties")
    if isinstance(props, dict):
        for spec in props.values():
            if isinstance(spec, dict) and isinstance(spec.get("description"), str):
                spec["description"] = _first(spec["description"], SHORT_PROPERTY)
    return out


def _first(text: str, limit: int) -> str:
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def rules_for(rules: str, offered: set[str], *, history: bool) -> str:
    """The owner rules that apply to the offered tools. Lines about tools not offered, and about the parts of the
    prompt the compact tier leaves out, are dropped; the limits on what Ava may do herself are always kept."""
    kept = []
    for line in rules.splitlines():
        if any(a in line for a in _ALWAYS):
            kept.append(line)
            continue
        tags = [tag for needle, tag in _RULE_TAGS if needle in line]
        if tags:
            if any(t != "-" and any(n.startswith(t) for n in offered) for t in tags):
                kept.append(line)
            continue
        if not history and any(k in line for k in _HISTORY_LINES):
            continue
        kept.append(line)
    return "\n".join(kept)


def cap_history(msgs: list[dict[str, Any]], turn_start: int, tier: str) -> tuple[list[dict[str, Any]], int]:
    """Compact keeps about 1,500 tokens of history, minimal the last two turns. Whole turns only, newest kept."""
    history, rest = list(msgs[:turn_start]), list(msgs[turn_start:])
    units = pb._units(history)  # noqa: SLF001  (one definition of a turn)
    if tier == MINIMAL:
        while units and sum(len(u) for u in units) > MINIMAL_HISTORY_MESSAGES:
            units.pop(0)
    else:
        while len(units) > 1 and sum(pb._tokens(u) for u in units) > COMPACT_HISTORY_TOKENS:  # noqa: SLF001
            units.pop(0)
        if units and pb._tokens(units[0]) > COMPACT_HISTORY_TOKENS:  # noqa: SLF001
            units = []
    kept = [m for u in units for m in u]
    if tier == MINIMAL and rest and isinstance(rest[0].get("content"), list):  # small talk needs no notes
        rest[0] = {**rest[0], "content": [b for b in rest[0]["content"] if not _is_note(b)]}
    return [*kept, *rest], len(kept)


def _is_note(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    return block.get("type") == "search_result" or str(block.get("text", "")).startswith("(Notes from")


def shape(
    window: cw.WindowInfo,
    *,
    text: str,
    pending: bool,
    waiting: str,
    system: list[SystemBlock],
    tools: list[dict[str, Any]],
    msgs: list[dict[str, Any]],
    turn_start: int,
    charter: str,
    rules: str,
    who: str,
    who_min: str,
    state: list[str],
) -> Shaped:
    """Apply the tier. `system` and `tools` are the full ones the loop built for this person; the full tier returns
    them as they are. `who` is the sentence that says who is speaking and that the chat is verified, and `state` the
    code-built lines about the moment (people waiting, the owner's status): both come from the loop's own code."""
    tier = select_tier(window, text, pending=pending or bool(waiting))
    if tier == FULL:
        return Shaped(FULL, system, tools, msgs, turn_start)
    msgs, turn_start = cap_history(msgs, turn_start, tier)
    lines = "\n\n".join([who, *[s for s in state if s]])
    if tier == MINIMAL:
        return Shaped(
            MINIMAL,
            [
                SystemBlock(charter + "\n\n" + MINIMAL_RULES, cache=True),
                SystemBlock("\n\n".join([who_min, *[x for x in state if x]])),
            ],
            [],
            msgs,
            turn_start,
        )
    chosen = choose_tools(tools, text, waiting=bool(waiting), pending=pending)
    offered = {str(t["name"]) for t in chosen}
    body = rules_for(rules, offered, history=turn_start > 0)
    return Shaped(
        COMPACT, [SystemBlock(charter + "\n\n" + body, cache=True), SystemBlock(lines)], chosen, msgs, turn_start
    )
