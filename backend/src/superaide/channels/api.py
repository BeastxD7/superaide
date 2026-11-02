"""The public entry point of `channels/` for other packages (ADR 0015): what the owner's tool loop may use."""

from superaide.channels.activity import CHANNEL_ACTIVITY_TOOL
from superaide.channels.activity import report as channel_activity_report

__all__ = ["CHANNEL_ACTIVITY_TOOL", "channel_activity_report"]
