"""Who wins when two facts disagree (issue #46). Decided in code, never by the model.

A lower number is more trusted. A newer fact replaces an older one only when its rank is equal or
better; a weaker claim waits in the notebook as a "which is current?" question for the owner, so
"newest wins" can't be used to poison memory.
"""

from __future__ import annotations

OWNER_VERIFIED = 1  # the owner, on a verified channel (chat, Telegram, Slack, the introduction, an edit)
OWNER_DOCUMENT = 2  # a file the owner shared
OWNER_LINK = 3  # a page or repository the owner pointed at
THIRD_PARTY = 4  # someone else's claim ("remember that…" from a colleague)
OTHER_WEB = 5  # any other web content

SOURCE_RANK = {
    "chat": OWNER_VERIFIED,
    "telegram": OWNER_VERIFIED,
    "slack": OWNER_VERIFIED,
    "intro_chat": OWNER_VERIFIED,
    "intro_call": OWNER_VERIFIED,
    "owner_edit": OWNER_VERIFIED,
    "document": OWNER_DOCUMENT,
    "link": OWNER_LINK,
}

# What a web page may teach Ava. Anything else (urgency, sharing, availability, preferences) comes from
# the owner: a page anyone can edit must not change what Ava does (untrusted data never steers control).
LINK_CATEGORIES = {"about", "work"}

# conflict_status: none (live) | pending (a weaker claim against a stronger fact) | review (a link fact
# outside LINK_CATEGORIES) | resolved (the owner decided)
VISIBLE_STATUS = "none"


def rank_for(source: str) -> int:
    return SOURCE_RANK.get(source, OWNER_VERIFIED)


def needs_review(source: str, category: str) -> bool:
    return source == "link" and category not in LINK_CATEGORIES
