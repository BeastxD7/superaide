"""What other packages may use from reception (ADR 0015): the share card's facts, never its internals."""

from superaide.platform.sandbox_guard import active as sandbox_active
from superaide.reception.card import FIELDS, HELP, PLACE, RULES, SETTINGS_PATH
from superaide.reception.inbox import INBOX_TOOL, QUOTE_MARK, awareness_line
from superaide.reception.inbox import report as inbox_report
from superaide.reception.relay import RELAY_TOOL
from superaide.reception.relay import pending_sentences as relay_pending
from superaide.reception.relay import request as relay_request
from superaide.reception.resilience import BUDGET as MODEL_BUDGET
from superaide.reception.resilience import ERROR as MODEL_ERROR
from superaide.reception.resilience import JOB as VISITOR_REPLY_JOB
from superaide.reception.resilience import MAX_ATTEMPTS as VISITOR_REPLY_ATTEMPTS
from superaide.reception.resilience import queue_reply as queue_visitor_reply
from superaide.reception.resilience import record_model as record_model_health
from superaide.reception.resilience import status as outage_status
from superaide.reception.service import first_name_shared, set_first_name_shared, share_card_state

__all__ = [
    "sandbox_active",
    "MODEL_BUDGET",
    "MODEL_ERROR",
    "VISITOR_REPLY_ATTEMPTS",
    "VISITOR_REPLY_JOB",
    "outage_status",
    "queue_visitor_reply",
    "record_model_health",
    "FIELDS",
    "HELP",
    "INBOX_TOOL",
    "QUOTE_MARK",
    "RELAY_TOOL",
    "relay_request",
    "relay_pending",
    "awareness_line",
    "inbox_report",
    "PLACE",
    "RULES",
    "SETTINGS_PATH",
    "first_name_shared",
    "set_first_name_shared",
    "share_card_state",
]
