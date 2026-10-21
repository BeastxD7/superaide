"""Trust tiers. Higher tiers unlock more sensitive actions."""

from enum import IntEnum


class Trust(IntEnum):
    HINT = 0  # a display name, caller ID or a claim in text: unlocks nothing
    AUTHENTICATED = 1  # verified platform ID, not linked to a person: stranger mode
    LINKED = 2  # linked to a person through a verified step on a trusted channel
    DIRECTORY = 3  # bound to a company directory account (later)
    STEPPED_UP = 4  # fresh step-up verification for money, access or identity changes
