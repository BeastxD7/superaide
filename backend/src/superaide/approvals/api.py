"""What other packages may use from approvals (ADR 0015): sealing a new approval row to the content shown, and the
approval-fatigue numbers (#407)."""

from superaide.approvals import batching, burden
from superaide.approvals.binding import seal

__all__ = ["batching", "burden", "seal"]
