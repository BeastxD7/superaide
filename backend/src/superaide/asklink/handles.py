"""Which handles an owner may pick for their Ask link.

3 to 30 characters: lowercase letters, digits and single hyphens, starting and ending with a letter
or digit. Reserved words (our own paths, roles that suggest staff) and profanity are refused, as are
names that pass themselves off as Superaide or a model vendor. The check runs on a folded form too, so
"4dm1n" and "s-h-i-t" are caught. A suggested handle is pseudonymous ("ava-k3q9tz").
"""

from __future__ import annotations

import re
import secrets

MIN_LEN = 3
MAX_LEN = 30
PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9]))*$")

RESERVED = frozenset(
    """
    a about abuse account accounts admin administrator agent agents ai api app apps ask assistant auth
    ava billing blog console contact dashboard docs help home hire legal login logout me meet mod
    moderator new null official operator owner p privacy report root security settings signin signup
    staff static support system team terms test undefined verify www workspace
    """.split()
)
# Anyone using these would look like us or a model vendor talking.
IMPERSONATION = ("superaide", "anthropic", "openai")
STAFF_WORDS = frozenset({"admin", "official", "support", "staff", "moderator", "claude", "superaide"})
# Kept short on purpose. Roots that are rude wherever they appear are matched inside the handle;
# words that hide inside ordinary ones ("grape", "peacock", "Scunthorpe") only as a whole part.
PROFANE_ROOTS = ("fuck", "shit", "nigg", "fagg", "whore", "wank", "twat", "asshole", "retard", "porn")
PROFANE_WORDS = frozenset(
    {"cunt", "bitch", "slut", "rape", "nazi", "dick", "cock", "pussy", "kike", "spic", "bastard", "penis", "sex"}
)
_FOLD = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g", "-": None})
_SUGGEST = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


class HandleError(ValueError):
    pass


def normalize(raw: str) -> str:
    return (raw or "").strip().lower()


def check(raw: str) -> str:
    """The handle to store, or HandleError with a reason the owner can act on."""
    h = normalize(raw)
    if not MIN_LEN <= len(h) <= MAX_LEN:
        raise HandleError(f"Use {MIN_LEN} to {MAX_LEN} characters.")
    if not PATTERN.match(h):
        raise HandleError(
            "Use lowercase letters, numbers and single hyphens, starting and ending with a letter or number."
        )
    folded = h.translate(_FOLD)
    parts = {p.translate(_FOLD) for p in h.split("-")} | set(h.split("-"))
    if h in RESERVED or h.replace("-", "") in RESERVED or folded in RESERVED:
        raise HandleError("That one is reserved. Try another.")
    if any(w in folded or w in h.replace("-", "") for w in IMPERSONATION) or parts & STAFF_WORDS:
        raise HandleError("That looks like an official account. Try another.")
    if any(w in folded for w in PROFANE_ROOTS) or parts & PROFANE_WORDS or folded in PROFANE_WORDS:
        raise HandleError("Pick a handle without rude words.")
    return h


def suggest() -> str:
    """A pseudonymous handle that passes `check`: random letters can spell a rude word (or a reserved
    one), so a suggestion is tried against the same rules an owner's own handle must meet."""
    for _ in range(100):
        candidate = "ava-" + "".join(secrets.choice(_SUGGEST) for _ in range(6))
        try:
            return check(candidate)
        except HandleError:
            continue
    raise RuntimeError("couldn't generate a handle that passes the handle rules")  # not reachable in practice
