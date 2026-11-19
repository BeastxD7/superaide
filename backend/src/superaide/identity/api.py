"""What other packages may use from identity (ADR 0015): reading a name claim out of a message."""

from superaide.identity.linking import detect_claimed_name

__all__ = ["detect_claimed_name"]
