"""The context-card schema: the only thing the server accepts from an import (#133).

A third-party export (a Takeout slice, a chat archive, a health export) is converted on the
owner's own machine into small cards; the server never receives the raw file. Every card is
checked here, whatever sent it: unknown fields are refused, text is capped, control characters
are stripped, and a card whose scope the owner did not allow in the lease is refused. What a
card may be seen by is not in the schema at all; the lease service decides that (owner only).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCOPES = ("calendar", "contacts", "notes", "chat", "health", "location", "email", "other")
SOURCE_KINDS = ("takeout", "chat_archive", "health", "notes", "workspace")
CATEGORIES = ("about", "work", "person", "preference", "urgency", "availability", "other")

MAX_CARD_TEXT = 600  # a card is a few sentences, not a pasted document
MAX_TITLE = 120
MAX_CARDS_PER_REQUEST = 500
MAX_CARDS_PER_LEASE = 2000
MIN_DAYS, DEFAULT_DAYS, MAX_DAYS = 1, 7, 30
IMPORT_CONFIDENCE = 0.4  # below anything the owner said to Ava, and below a memory passport (0.6)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{6,}\d(?![\w])")
DATE_SHAPE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")


def clean(text: str) -> str:
    return " ".join(_CONTROL.sub(" ", text).split())


def has_contact_details(text: str) -> bool:
    """An address or a number is another person's data: such a card is left out, not imported."""
    if EMAIL.search(text):
        return True
    for m in PHONE.finditer(text):
        value = m.group(0).strip()
        digits = sum(c.isdigit() for c in value)
        # A date or a bare run of digits (an order number) is not a phone number.
        if 8 <= digits <= 15 and not DATE_SHAPE.match(value) and not value.isdigit():
            return True
    return False


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scope: str = Field(max_length=30)
    title: str = Field(default="", max_length=MAX_TITLE)
    text: str = Field(min_length=1, max_length=MAX_CARD_TEXT)
    category: str = Field(default="other", max_length=20)
    when: date | None = None

    @field_validator("text", "title")
    @classmethod
    def _tidy(cls, v: str) -> str:
        return clean(v)


class LeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=200)
    source_kind: Literal["takeout", "chat_archive", "health", "notes", "workspace"]
    scopes: list[Literal["calendar", "contacts", "notes", "chat", "health", "location", "email", "other"]] = Field(
        min_length=1, max_length=len(SCOPES)
    )
    days: int = Field(default=DEFAULT_DAYS, ge=MIN_DAYS, le=MAX_DAYS)


class CardBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[Card] = Field(min_length=1, max_length=MAX_CARDS_PER_REQUEST)
