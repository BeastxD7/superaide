"""The share card: what the receptionist may tell a stranger about the owner (issue #475).

The owner types four values (company, role, city, public email) and switches each one on separately; every switch
is off by default. Code builds the card from those columns: only a value whose switch is on and that is not empty is
ever put in the receptionist's prompt, so nothing is inferred, remembered or guessed, and the model cannot add one.
The first name is a separate choice (`ReceptionSettings.show_first_name`, also off by default).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.identity.models import Person
from superaide.memory.models import OwnerPreference

# Where the owner edits it, named as the app's own nav shows it. One place so the wording cannot drift.
PLACE = "Settings → People you don't know → Your share card"
SETTINGS_PATH = "/dashboard/settings#share-card"
NUDGE_EVERY = timedelta(hours=24)
_EMAIL = re.compile(r"^[^@\s,;<>]{1,64}@[^@\s,;<>]{1,190}\.[^@\s,;<>]{2,}$")


@dataclass(frozen=True)
class Field:
    key: str  # company | role | city | email
    label: str
    limit: int


FIELDS = (
    Field("company", "Company", 120),
    Field("role", "Role", 120),
    Field("city", "City", 120),
    Field("email", "Public email", 200),
)


HELP = {
    "company": "Lets Ava tell someone she doesn't know where you work. Example: 'Larkfield'.",
    "role": "Lets Ava tell someone she doesn't know what you do. Example: 'Designer'.",
    "city": "Lets Ava tell someone she doesn't know which city you are based in. Not where you are right now.",
    "email": "Lets Ava give someone she doesn't know an email address you chose for this, one you don't mind sharing.",
}

# Shown read-only on the settings page ("How this works") and in docs/settings.md.
RULES = (
    "Someone Ava doesn't know is told only the card values you switch on, and your first name only if you choose it. "
    "Every switch starts off.",
    "Nothing is guessed or taken from your notes, email or calendar: she says only the exact words typed here.",
    "Ava can't change the card herself. If you ask her in chat, she points you to this page.",
    "She never says where you are, what you are doing or whether you are free, whatever the card says.",
)


def clean(key: str, raw: object) -> str:
    """One line, no control characters, cut to the field's length. A public email must look like one."""
    f = next(x for x in FIELDS if x.key == key)
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(raw or "")).split())[: f.limit]
    if key == "email" and text and not _EMAIL.match(text):
        raise ValueError("That doesn't look like an email address.")
    return text


def value(pref: OwnerPreference | None, key: str) -> str:
    return str(getattr(pref, f"card_{key}", "") or "") if pref is not None else ""


def public(pref: OwnerPreference | None, key: str) -> bool:
    return bool(getattr(pref, f"card_{key}_public", False)) if pref is not None else False


def lines(pref: OwnerPreference | None) -> list[str]:
    """Exactly what the receptionist is told: "Label: value" for each value that is switched on and not empty."""
    return [f"{f.label}: {v}" for f in FIELDS if public(pref, f.key) and (v := value(pref, f.key).strip())]


def view(pref: OwnerPreference | None) -> list[dict[str, object]]:
    return [
        {"key": f.key, "label": f.label, "value": value(pref, f.key), "public": public(pref, f.key)} for f in FIELDS
    ]


def preference(session: Session, owner: Person) -> OwnerPreference | None:
    return session.scalar(select(OwnerPreference).where(OwnerPreference.person_id == owner.id))
