"""The public entry point of `channels/` for other packages (ADR 0015): what the owner's tool loop (the activity
report) and the pipeline and app chat (reading a sent file) may use."""

from superaide.channels.activity import CHANNEL_ACTIVITY_TOOL, ROOM_MARK
from superaide.channels.activity import report as channel_activity_report
from superaide.channels.attachments import (
    IMAGE_NOT_READ,
    NOT_READABLE_KIND,
    Read,
    marker,
    read_message,
    reasons_text,
    sniff,
    too_big_message,
)

__all__ = [
    "CHANNEL_ACTIVITY_TOOL",
    "IMAGE_NOT_READ",
    "NOT_READABLE_KIND",
    "ROOM_MARK",
    "Read",
    "channel_activity_report",
    "marker",
    "read_message",
    "reasons_text",
    "sniff",
    "too_big_message",
]
