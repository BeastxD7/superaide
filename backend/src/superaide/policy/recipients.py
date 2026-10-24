"""Who an outbound message would really reach (issue #327), decided in code.

Three small, separate lists live here so tests and the pre-flight share one source:
- AUTOMATED_LOCAL_PARTS: addresses that look like no person reads them (a hard backstop under the pre-flight);
- NOT_MONITORED_PHRASES: what a mail says about its own sender address;
- HEDGES: phrases Ava uses when she doubts a message will reach a person. A reply that contains one must
  never come with a created approval (warning comes before asking, never inside it).
"""

from __future__ import annotations

import re

AUTOMATED_LOCAL_PARTS = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer[-_.]?daemon|postmaster|bounces?([-_+].*)?|"
    r"notifications?|alerts?)$",
    re.IGNORECASE,
)
# notifications@ and alerts@ are automated only on a vendor's domain, not at a person's own company mail.
_VENDOR_ONLY = re.compile(r"^(notifications?|alerts?)$", re.IGNORECASE)
_FREE_MAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "proton.me", "protonmail.com"}

NOT_MONITORED_PHRASES = [
    "not monitored",
    "unmonitored",
    "do not reply",
    "don't reply",
    "please do not respond",
    "no-reply",
    "noreply",
    "this mailbox is not",
    "replies to this email are not",
    "automated message",
]
_ROUTE_PHRASES = re.compile(
    r"contact (us|support) (at|via|through|on)|visit our (contact|support|help)|call us|support portal|"
    r"submit a (ticket|request)|contact page|live chat",
    re.IGNORECASE,
)

HEDGES = [
    "may not reach",
    "might not reach",
    "may not be read",
    "might not be read",
    "may not be monitored",
    "might not be monitored",
    "not sure it will reach",
    "not sure this will reach",
    "not sure anyone will read",
    "may not reach anyone",
    "might not reach anyone",
    "may not reach a person",
    "might not reach a person",
    "may not get to a person",
    "unlikely to reach",
    "no guarantee it will reach",
    "no guarantee anyone will read",
    "not certain it will reach",
    "probably won't reach",
    "may go to a mailbox nobody reads",
]


def has_hedge(text: str) -> bool:
    low = (text or "").lower().replace("’", "'")
    return any(h in low for h in HEDGES)


def automated_address(address: str) -> bool:
    local, _, dom = (address or "").lower().partition("@")
    if not local or not dom:
        return False
    if _VENDOR_ONLY.match(local):
        return dom not in _FREE_MAIL
    return bool(AUTOMATED_LOCAL_PARTS.match(local))


def says_not_monitored(text: str) -> bool:
    low = (text or "").lower().replace("’", "'")
    return any(p in low for p in NOT_MONITORED_PHRASES)


def names_another_route(text: str) -> bool:
    return bool(_ROUTE_PHRASES.search(text or ""))


AUTOMATED_CHIP = "This address looks automated; a reply is probably not read."
UNSURE_CHIP = "Ava could not confirm that a person reads this."
_GENERIC = {
    "com", "net", "org", "mail", "mailer", "email", "info", "support", "account", "accounts", "noreply", "reply",
    "no-reply", "donotreply", "hello", "team", "help", "customer", "service", "services", "from", "your", "bank",
    "online", "marketing", "notifications", "alerts", "news", "newsletter",
}  # fmt: skip


def owner_named(typed: str, address: str, display_name: str = "") -> bool:
    """Whether the owner's own words name this recipient: the address itself, or the sender's name or the
    company in their domain ("say thanks to marlin"). This is how code tells "the owner asked for this
    recipient" from "the model picked it": the model's say-so is never used."""
    low = (typed or "").lower()
    if not low or not address:
        return False
    if address.lower() in low:
        return True
    dom = address.lower().partition("@")[2]
    words = {w for w in re.split(r"[^a-z0-9]+", dom + " " + (display_name or "").lower()) if len(w) >= 4}
    return any(w in low for w in words - _GENERIC)
