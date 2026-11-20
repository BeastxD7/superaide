"""Tone of drafted replies (#683, refs #486 and #497).

The tone is a fixed choice from `TONES`, stored on `owner_preference`. The owner sees it and can change it in
settings; until she does, code learns it from how she writes (her own messages to Ava, no consent gate: that is the
owner's decision for voice and tone). The model never receives free text from the owner or a stranger here, only the
fixed sentence of the chosen tone, and the tone is applied to drafts only: it never reaches identity, trust, memory
audience or what may run, and a draft still says nothing a stranger may not be told."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from superaide.identity.models import Person
from superaide.memory.api import preferences_for
from superaide.memory.models import Event, OwnerPreference
from superaide.platform.audit import audit

# value -> (label shown to the owner, sentence added to the drafting prompt). Written in code, reviewed like code.
TONES: dict[str, tuple[str, str]] = {
    "neutral": ("Neutral", "Keep a plain, polite tone."),
    "warm": ("Warm", "Keep a warm, friendly tone, as one would write to a friend. No emoji."),
    "formal": ("Formal", "Keep a formal, courteous tone, as one would write to a client."),
    "brief": ("Brief", "Be as short as possible: one short sentence, no pleasantries."),
}
SOURCES = ("default", "observed", "owner")
SAMPLE = 40  # the owner's most recent messages looked at
MIN_SAMPLE = 8  # fewer than this and nothing is learned
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
_FORMAL = re.compile(r"\b(dear|regards|kindly|sincerely|please find|would you|could you please)\b", re.I)
_WARM = re.compile(r"\b(thanks|thank you|hey|hi|love|cheers|appreciate|haha|lol)\b", re.I)


class ToneError(ValueError):
    pass


def observe(texts: list[str]) -> str | None:
    """The tone these messages suggest, or None when there are too few to say. Pure: no database, no model."""
    texts = [t.strip() for t in texts if t and t.strip()]
    if len(texts) < MIN_SAMPLE:
        return None
    words = sum(len(t.split()) for t in texts) / len(texts)
    emoji = sum(bool(_EMOJI.search(t)) or t.count("!") > 0 for t in texts) / len(texts)
    formal = sum(bool(_FORMAL.search(t)) for t in texts) / len(texts)
    warm = sum(bool(_WARM.search(t)) for t in texts) / len(texts)
    if formal >= 0.25:
        return "formal"
    if emoji >= 0.3 or warm >= 0.4:
        return "warm"
    if words <= 5:
        return "brief"
    return "neutral"


def learn(session: Session, owner: Person) -> OwnerPreference:
    """Update the tone from how the owner writes, unless she chose one herself. Returns her preferences."""
    pref = preferences_for(session, owner)
    if pref.draft_tone_source == "owner":
        return pref
    rows = session.scalars(
        select(Event.content)
        .where(Event.direction == "in", Event.resolved_person_id == owner.id, Event.erased_at.is_(None))
        .order_by(Event.created_at.desc())
        .limit(SAMPLE)
    )
    seen = observe(list(rows))
    if seen is not None and seen != pref.draft_tone:
        pref.draft_tone, pref.draft_tone_source = seen, "observed"
        audit(session, "briefing", "tone_observed", tone=seen)
    return pref


def view(pref: OwnerPreference) -> tuple[str, str, list[tuple[str, str]]]:
    """(tone, source, [(value, label)]) as shown to the owner."""
    tone = pref.draft_tone if pref.draft_tone in TONES else "neutral"
    source = pref.draft_tone_source if pref.draft_tone_source in SOURCES else "default"
    return tone, source, [(v, label) for v, (label, _) in TONES.items()]


def set_tone(session: Session, owner: Person, tone: str | None) -> OwnerPreference:
    """The owner picks a tone, or `None` to let it be learned again."""
    pref = preferences_for(session, owner)
    if tone is None:
        pref.draft_tone, pref.draft_tone_source = "neutral", "default"
        audit(session, "briefing", "tone_reset")
        return learn(session, owner)
    if tone not in TONES:
        raise ToneError("Unknown tone")
    pref.draft_tone, pref.draft_tone_source = tone, "owner"
    audit(session, "briefing", "tone_set", tone=tone)
    return pref


def instruction(pref: OwnerPreference) -> str:
    """The fixed sentence for the prompt: a choice from `TONES`, never the owner's or a stranger's words."""
    return TONES.get(pref.draft_tone, TONES["neutral"])[1]
