"""What other packages may use from reception (ADR 0015): the share card's facts, never its internals."""

from superaide.reception.card import FIELDS, HELP, PLACE, RULES, SETTINGS_PATH
from superaide.reception.inbox import INBOX_TOOL, QUOTE_MARK, awareness_line
from superaide.reception.inbox import report as inbox_report
from superaide.reception.service import first_name_shared, set_first_name_shared, share_card_state

__all__ = [
    "FIELDS",
    "HELP",
    "INBOX_TOOL",
    "QUOTE_MARK",
    "awareness_line",
    "inbox_report",
    "PLACE",
    "RULES",
    "SETTINGS_PATH",
    "first_name_shared",
    "set_first_name_shared",
    "share_card_state",
]
