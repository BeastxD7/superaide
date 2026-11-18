"""What other packages may use from reception (ADR 0015): the share card's facts, never its internals."""

from superaide.reception.card import FIELDS, HELP, PLACE, RULES, SETTINGS_PATH
from superaide.reception.service import first_name_shared, set_first_name_shared, share_card_state

__all__ = [
    "FIELDS",
    "HELP",
    "PLACE",
    "RULES",
    "SETTINGS_PATH",
    "first_name_shared",
    "set_first_name_shared",
    "share_card_state",
]
