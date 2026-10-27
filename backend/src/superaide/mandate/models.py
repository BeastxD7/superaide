"""Tables of `mandate/` (ADR 0015 phase 7): moved out of platform/db/models.py unchanged."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from superaide.platform.db.models import Base, Scoped


class Mandate(Scoped, Base):
    """A mandate card the owner signed (issue #112): the token, the card text, and how often it was used."""

    __tablename__ = "mandate"

    token: Mapped[str] = mapped_column(Text)
    card: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | revoked
    uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Delegation (#112): a child is its parent's token with more restrictions added. Revoking a parent revokes
    # its children, and a child runs only while every ancestor is active, unexpired and not used up.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mandate.id"), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(120), default="", server_default="")
